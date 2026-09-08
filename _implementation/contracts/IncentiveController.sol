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

    /**
     * @notice Aktualisiert den Reputationsscore basierend auf der Abweichung.
     *
     *  TODO (Teams):
     *    1. Hole forecast und actual für (household, slot)
     *    2. Berechne Abweichung in Promille:
     *       deviation = |actual - forecast| * 1000 / forecast
     *    3. Wende eine Score-Update-Strategie an, z.B.:
     *         - deviation < 100 (10%):  score += 20  (bis max 1000)
     *         - deviation 100-250:      score += 0   (neutral)
     *         - deviation > 250:        score -= 30  (min 0)
     *    4. Emit ScoreUpdated mit der Abweichung
     *
     *  Alternative Designs (für Bonuspunkte):
     *    - Exponentielle Strafe für grosse Abweichungen
     *    - Score-Decay über Zeit (Verfall ohne Aktivität)
     *    - Community-Score: Mittelwert über Gruppe statt einzeln
     */
    function _updateScoreForSlot(address household, uint256 slot) internal {
        Forecast memory f = forecasts[household][slot];
        Actual memory a = actuals[household][slot];

        if (f.timestamp == 0 || f.expectedConsumptionWh == 0) return;
        if (a.actualConsumptionWh == 0 && a.actualProductionWh == 0) return;

        // TODO: Implementierung durch Team
        //
        // uint256 deviation = _calculateDeviation(f.expectedConsumptionWh, a.actualConsumptionWh);
        // uint256 currentScore = reputationScore[household];
        //
        // if (deviation < 100) {
        //     reputationScore[household] = _min(currentScore + 20, 1000);
        // } else if (deviation > 250) {
        //     reputationScore[household] = currentScore > 30 ? currentScore - 30 : 0;
        // }
        //
        // emit ScoreUpdated(household, reputationScore[household], deviation);
    }

    // ─────────────────────────────────────────────────────────────
    //  Preisfaktor (wird vom P2PEnergyMarket gelesen)
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Gibt den Preismultiplikator für einen Haushalt zurück.
     * @return multiplier in Promille (1000 = neutral, <1000 = Rabatt für Käufer)
     *
     *  TODO (Teams):
     *    - Mappe den Reputationsscore auf einen Multiplikator
     *    - Beispiel: score 1000 → multiplier 800 (20% Rabatt)
     *                score 500  → multiplier 1000 (neutral)
     *                score 0    → multiplier 1200 (20% Aufschlag)
     */
    function getPriceMultiplier(address household) external view returns (uint256 multiplier) {
        // TODO: Implementierung durch Team
        //
        // uint256 score = reputationScore[household];
        // if (score == 0) return BASE_MULTIPLIER; // Neuer Haushalt: neutral
        //
        // // Lineare Interpolation zwischen 800 und 1200
        // // score=1000 -> 800, score=500 -> 1000, score=0 -> 1200
        // if (score >= 500) {
        //     uint256 discount = ((score - 500) * MAX_DISCOUNT) / 500;
        //     return BASE_MULTIPLIER - discount;
        // } else {
        //     uint256 penalty = ((500 - score) * MAX_PENALTY) / 500;
        //     return BASE_MULTIPLIER + penalty;
        // }

        return BASE_MULTIPLIER; // Default: kein Incentive aktiv
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
