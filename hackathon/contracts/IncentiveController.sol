// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IIncentiveController.sol";

/**
 * @title IncentiveController
 * @notice Phase 3 (OPTIONAL): Belohnt Haushalte mit besseren Preisen,
 *         wenn ihr tatsächlicher Verbrauch der AI-Prognose entspricht.
 *
 * @dev STARTER-CODE - Teams designen das Incentive-Modell selbst.
 *      Mögliche Ansätze:
 *        A) Direkter Preismultiplikator (einfach)
 *        B) Reputationsscore über Zeit (mittel)
 *        C) Community-Pool mit geteiltem Bonus (anspruchsvoll)
 *
 *      Das Python-Skript `ai_forecast.py` schreibt Prognosen in diesen Contract.
 *      Bei der Settlement-Phase liest P2PEnergyMarket den Preisfaktor aus
 *      (siehe P2PEnergyMarket.sol, Feld `incentiveController` +
 *      `setIncentiveController()`).
 *
 *      Dieser Contract implementiert IIncentiveController, damit
 *      P2PEnergyMarket ihn über das Interface ansprechen kann, ohne den
 *      vollen Code zu kennen (gleiches Muster wie IBatteryManager in Phase 2).
 */
contract IncentiveController is IIncentiveController {

    // ─────────────────────────────────────────────────────────────
    //  Storage
    // ─────────────────────────────────────────────────────────────

    address public owner;
    mapping(address => bool) public authorizedAI;

    /// @notice Prognose pro Haushalt und Slot
    struct Forecast {
        uint256 expectedConsumptionWh;
        uint256 expectedProductionWh;
        uint256 slot;
        uint256 timestamp;
    }

    /// @notice Tatsächlicher Wert (nach dem Slot eingetragen)
    struct Actual {
        uint256 actualConsumptionWh;
        uint256 actualProductionWh;
        uint256 slot;
    }

    mapping(address => mapping(uint256 => Forecast)) public forecasts;
    mapping(address => mapping(uint256 => Actual)) public actuals;

    /// @notice Reputationsscore pro Haushalt (0-1000, Start: 500)
    mapping(address => uint256) public reputationScore;

    /// @notice Basispreis-Multiplikator in Promille (1000 = 100%, also normaler Preis)
    uint256 public constant BASE_MULTIPLIER = 1000;

    /// @notice Maximaler Rabatt: 200 Promille = 20%
    uint256 public constant MAX_DISCOUNT = 200;

    /// @notice Maximaler Aufschlag: 200 Promille = 20%
    uint256 public constant MAX_PENALTY = 200;

    /// @notice Abweichung bis hierhin gilt als treffsicher (100 Promille = 10%)
    uint256 public constant GOOD_DEVIATION = 100;

    /// @notice Ab hier gilt die Prognose als wertlos (500 Promille = 50%)
    uint256 public constant BAD_DEVIATION = 500;

    /// @notice Glaettungsfenster des gleitenden Durchschnitts
    uint256 public constant SMOOTHING = 8;

    // ─────────────────────────────────────────────────────────────
    //  Events
    // ─────────────────────────────────────────────────────────────

    event ForecastSubmitted(address indexed household, uint256 slot, uint256 expectedConsumption);
    event ActualSubmitted(address indexed household, uint256 slot, uint256 actualConsumption);
    event ScoreUpdated(address indexed household, uint256 newScore, uint256 deviation);
    event AIAuthorized(address indexed ai);

    // ─────────────────────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    modifier onlyAI() {
        require(authorizedAI[msg.sender], "Not authorized AI");
        _;
    }

    // ─────────────────────────────────────────────────────────────
    //  Constructor
    // ─────────────────────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
        authorizedAI[msg.sender] = true;
    }

    function authorizeAI(address ai) external onlyOwner {
        authorizedAI[ai] = true;
        emit AIAuthorized(ai);
    }

    // ─────────────────────────────────────────────────────────────
    //  Prognose & Ist-Wert eintragen
    // ─────────────────────────────────────────────────────────────

    function submitForecast(
        address household,
        uint256 slot,
        uint256 expectedConsumptionWh,
        uint256 expectedProductionWh
    ) external onlyAI {
        forecasts[household][slot] = Forecast({
            expectedConsumptionWh: expectedConsumptionWh,
            expectedProductionWh: expectedProductionWh,
            slot: slot,
            timestamp: block.timestamp
        });

        // Initial-Score auf 500 setzen, falls noch keiner existiert
        if (reputationScore[household] == 0) {
            reputationScore[household] = 500;
        }

        emit ForecastSubmitted(household, slot, expectedConsumptionWh);
    }

    function submitActual(
        address household,
        uint256 slot,
        uint256 actualConsumptionWh,
        uint256 actualProductionWh
    ) external onlyAI {
        actuals[household][slot] = Actual({
            actualConsumptionWh: actualConsumptionWh,
            actualProductionWh: actualProductionWh,
            slot: slot
        });
        emit ActualSubmitted(household, slot, actualConsumptionWh);

        // Automatisch Score aktualisieren wenn beide Werte vorhanden
        _updateScoreForSlot(household, slot);
    }

    // ─────────────────────────────────────────────────────────────
    //  Score-Update
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Aktualisiert den Reputationsscore anhand der Prognoseguete.
     *
     * Bewertung eines einzelnen Slots (slotScore, 0-1000):
     *   Abweichung <= 100 Promille (10%)  ->  1000 Punkte
     *   Abweichung >= 500 Promille (50%)  ->     0 Punkte
     *   dazwischen                        ->  linear interpoliert
     *
     * Die 10-Prozent-Schwelle ist bewusst gewaehlt: Das Rauschen im
     * Verbrauchssimulator liegt bei plus/minus 15 Prozent, die mittlere
     * Abweichung also bei rund 7.5 Prozent. Wer seine Flexibilitaet
     * steuert, bleibt darunter. Wer nur raet, nicht.
     *
     * Der Gesamtscore ist ein gleitender Durchschnitt ueber acht Slots:
     *
     *   neu = (alt * 7 + slotScore) / 8
     *
     * Drei Gruende dafuer. Ein einzelner Ausreisser zerstoert die
     * Reputation nicht - das waere unfair, weil auch ein guter Agent mal
     * danebenliegt. Der Score reagiert trotzdem innerhalb weniger Slots
     * spuerbar. Und es braucht keine Historie im Storage, nur den letzten
     * Wert, was Gas spart.
     */
    function _updateScoreForSlot(address household, uint256 slot) internal {
        Forecast memory f = forecasts[household][slot];
        Actual memory a = actuals[household][slot];

        if (f.timestamp == 0 || f.expectedConsumptionWh == 0) return;
        if (a.actualConsumptionWh == 0 && a.actualProductionWh == 0) return;

        uint256 deviation = _calculateDeviation(
            f.expectedConsumptionWh, a.actualConsumptionWh);

        uint256 slotScore;
        if (deviation <= GOOD_DEVIATION) {
            slotScore = 1000;
        } else if (deviation >= BAD_DEVIATION) {
            slotScore = 0;
        } else {
            // Linear zwischen den beiden Schwellen abfallend
            slotScore = ((BAD_DEVIATION - deviation) * 1000) /
                        (BAD_DEVIATION - GOOD_DEVIATION);
        }

        uint256 alt = reputationScore[household];
        if (alt == 0) alt = 500;   // Neueinsteiger starten neutral

        reputationScore[household] = (alt * (SMOOTHING - 1) + slotScore) / SMOOTHING;

        emit ScoreUpdated(household, reputationScore[household], deviation);
    }

    // ─────────────────────────────────────────────────────────────
    //  Preisfaktor (wird vom P2PEnergyMarket gelesen)
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Preismultiplikator fuer einen Haushalt, in Promille.
     *
     * 1000 ist neutral, darunter ein Rabatt, darueber ein Aufschlag. Der
     * Market multipliziert den Basispreis damit, bevor er den Betrag vom
     * Konsumenten einzieht.
     *
     *   Score 1000  ->   800  (20 Prozent guenstiger)
     *   Score  500  ->  1000  (unveraendert)
     *   Score    0  ->  1200  (20 Prozent teurer)
     *
     * Die Spanne von plus/minus 20 Prozent ist gross genug, um Verhalten
     * zu beeinflussen, aber klein genug, dass niemand aus der Gemeinschaft
     * herausgepreist wird.
     */
    function getPriceMultiplier(address household)
        external view returns (uint256 multiplier)
    {
        uint256 score = reputationScore[household];

        // Haushalt ohne Historie zahlt den normalen Preis. Wichtig: Ein
        // neuer Teilnehmer soll nicht bestraft werden, nur weil er noch
        // keine Prognosen eingereicht hat.
        if (score == 0) return BASE_MULTIPLIER;

        if (score >= 500) {
            // Ueber dem Mittel: Rabatt, maximal 20 Prozent bei Score 1000
            uint256 discount = ((score - 500) * MAX_DISCOUNT) / 500;
            return BASE_MULTIPLIER - discount;
        }
        // Unter dem Mittel: Aufschlag, maximal 20 Prozent bei Score 0
        uint256 penalty = ((500 - score) * MAX_PENALTY) / 500;
        return BASE_MULTIPLIER + penalty;
    }

    // ─────────────────────────────────────────────────────────────
    //  Hilfsfunktionen (vorgegeben)
    // ─────────────────────────────────────────────────────────────

    /// @notice Berechnet die Abweichung in Promille (Fixedpoint-Arithmetik)
    function _calculateDeviation(uint256 expected, uint256 actual) internal pure returns (uint256) {
        if (expected == 0) return 0;
        uint256 diff = actual > expected ? actual - expected : expected - actual;
        return (diff * 1000) / expected;
    }

    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }

    // ─────────────────────────────────────────────────────────────
    //  View Functions
    // ─────────────────────────────────────────────────────────────

    function getReputationScore(address household) external view returns (uint256) {
        return reputationScore[household];
    }

    function getForecast(address household, uint256 slot) external view returns (Forecast memory) {
        return forecasts[household][slot];
    }

    function getActual(address household, uint256 slot) external view returns (Actual memory) {
        return actuals[household][slot];
    }
}
