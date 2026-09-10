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
    //  Score-Update  (HIER IMPLEMENTIEREN TEAMS)
    // ─────────────────────────────────────────────────────────────

    /// @notice Score-Gewinn bei sehr guter Prognose (Abweichung < GOOD_DEVIATION).
    uint256 public constant SCORE_REWARD = 20;

    /// @notice Score-Verlust bei schlechter Prognose (Abweichung > BAD_DEVIATION).
    uint256 public constant SCORE_PENALTY = 30;

    /// @notice Obere Score-Grenze.
    uint256 public constant SCORE_MAX = 1000;

    /// @notice Abweichungs-Schwelle (Promille) fuer "gute" Prognose -> Score-Gewinn.
    uint256 public constant GOOD_DEVIATION = 100;   // 10 %

    /// @notice Abweichungs-Schwelle (Promille) fuer "schlechte" Prognose -> Score-Verlust.
    uint256 public constant BAD_DEVIATION = 250;    // 25 %

    /**
     * @notice Aktualisiert den Reputationsscore basierend auf der Prognose-Abweichung.
     * @dev Design B (Reputationsscore ueber Zeit):
     *        1. Abweichung = gewichtetes Mittel aus Verbrauchs- (70 %) und
     *           Produktions-Abweichung (30 %), jeweils in Promille via
     *           _calculateDeviation(). Produktion zaehlt nur mit, wenn eine
     *           Produktions-Prognose > 0 vorlag (reine Konsumenten ohne PV
     *           werden nur am Verbrauch gemessen).
     *        2. Tiered Update:
     *             - Abweichung <  GOOD_DEVIATION (10 %):  Score += SCORE_REWARD (cap SCORE_MAX)
     *             - GOOD_DEVIATION .. BAD_DEVIATION:       neutral, kein Change
     *             - Abweichung >  BAD_DEVIATION (25 %):    Score -= SCORE_PENALTY (floor 0)
     *        3. emit ScoreUpdated(household, neuerScore, abweichung).
     *      Die Abweichungs-Schwellen sind bewusst asymmetrisch (Gewinn ab 10 %,
     *      Strafe erst ab 25 %), damit ein knapp daneben liegender Forecast nicht
     *      sofort bestraft wird - Prognosen sind naturgemaess ungenau.
     */
    function _updateScoreForSlot(address household, uint256 slot) internal {
        Forecast memory f = forecasts[household][slot];
        Actual memory a = actuals[household][slot];

        if (f.timestamp == 0 || f.expectedConsumptionWh == 0) return;
        if (a.actualConsumptionWh == 0 && a.actualProductionWh == 0) return;

        uint256 devConsumption = _calculateDeviation(f.expectedConsumptionWh, a.actualConsumptionWh);

        uint256 deviation = devConsumption;
        if (f.expectedProductionWh > 0) {
            uint256 devProduction = _calculateDeviation(f.expectedProductionWh, a.actualProductionWh);
            deviation = (devConsumption * 70 + devProduction * 30) / 100;
        }

        uint256 currentScore = reputationScore[household];
        if (currentScore == 0) currentScore = 500; // Absicherung, falls submitForecast uebersprungen wurde

        if (deviation < GOOD_DEVIATION) {
            currentScore = _min(currentScore + SCORE_REWARD, SCORE_MAX);
        } else if (deviation > BAD_DEVIATION) {
            // Floor bei 1, nicht 0: ein bestrafter Haushalt bleibt bestraft
            // (Score 0 gilt in getPriceMultiplier als "noch nie bewertet" -> neutral).
            currentScore = currentScore > SCORE_PENALTY + 1 ? currentScore - SCORE_PENALTY : 1;
        }

        reputationScore[household] = currentScore;
        emit ScoreUpdated(household, currentScore, deviation);
    }

    // ─────────────────────────────────────────────────────────────
    //  Preisfaktor (wird vom P2PEnergyMarket gelesen)
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Gibt den Preismultiplikator für einen Haushalt zurück.
     * @return multiplier in Promille (1000 = neutral, <1000 = Rabatt für Käufer)
     *
     * @dev Lineare Interpolation Score -> Multiplikator, gespiegelt um den
     *      Startscore 500:
     *        score 1000 -> 1000 - MAX_DISCOUNT = 800  (20 % Rabatt)
     *        score  500 -> 1000                        (neutral)
     *        score    1 -> ~1000 + MAX_PENALTY = ~1200 (20 % Aufschlag)
     *      Ein noch nie bewerteter Haushalt (score == 0) ist neutral - er soll
     *      weder belohnt noch bestraft werden, bevor ueberhaupt eine Prognose
     *      abgeglichen wurde.
     */
    function getPriceMultiplier(address household) external view returns (uint256 multiplier) {
        uint256 score = reputationScore[household];
        if (score == 0) return BASE_MULTIPLIER; // Neuer Haushalt: neutral

        if (score >= 500) {
            uint256 discount = ((score - 500) * MAX_DISCOUNT) / 500;
            return BASE_MULTIPLIER - discount;
        } else {
            uint256 penalty = ((500 - score) * MAX_PENALTY) / 500;
            return BASE_MULTIPLIER + penalty;
        }
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
