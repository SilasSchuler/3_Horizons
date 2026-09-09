// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IOracleStorage.sol";
import "./interfaces/IBatteryManager.sol";

/**
 * @title BatteryManager
 * @notice Phase 2: Lade-/Entladestrategie für Haushaltsbatterien.
 * @dev STARTER-CODE - Teams implementieren die Optimierungslogik.
 *
 *      Idee: Der Contract liest Wetterprognose und aktuellen SoC,
 *            entscheidet pro Slot, ob die Batterie geladen, entladen
 *            oder leer/voll bleibt, und protokolliert die Entscheidung.
 *
 *      Wichtig: Die simulierte SoC-Kurve im OracleStorage läuft unabhängig
 *      von diesen Entscheidungen weiter (sie folgt im Simulator nur
 *      production/consumption) - dieser Contract kann sie NICHT zurückschreiben.
 *      Wirksam wird eure Entscheidung stattdessen dadurch, dass
 *      P2PEnergyMarket.settleSlot() optional decideAction() aufruft und die
 *      gehandelte Energiemenge entsprechend anpasst (siehe P2PEnergyMarket.sol,
 *      Feld `batteryManager` + `setBatteryManager()`).
 *
 *      Dieser Contract implementiert IBatteryManager, damit P2PEnergyMarket
 *      ihn über das Interface ansprechen kann, ohne den vollen Code zu kennen.
 */
contract BatteryManager is IBatteryManager {

    // ─────────────────────────────────────────────────────────────
    //  Storage
    // ─────────────────────────────────────────────────────────────

    IOracleStorage public immutable oracle;
    address public owner;

    /// @notice Letzte protokollierte Entscheidung pro Haushalt
    struct Decision {
        Action action;
        uint256 amountWh;
        uint256 slot;
        uint256 timestamp;
    }

    mapping(address => Decision) public lastDecision;
    mapping(address => bool) public isManaged;
    address[] public managedHouseholds;

    // ─────────────────────────────────────────────────────────────
    //  Events
    // ─────────────────────────────────────────────────────────────

    event HouseholdManaged(address indexed household);
    event DecisionMade(
        address indexed household,
        Action action,
        uint256 amountWh,
        uint256 slot,
        string reason
    );

    // ─────────────────────────────────────────────────────────────
    //  Constructor
    // ─────────────────────────────────────────────────────────────

    constructor(address _oracle) {
        oracle = IOracleStorage(_oracle);
        owner = msg.sender;
    }

    function addHousehold(address household) external {
        require(msg.sender == owner, "Only owner");
        require(!isManaged[household], "Already managed");
        require(oracle.isHouseholdRegistered(household), "Not in oracle");
        isManaged[household] = true;
        managedHouseholds.push(household);
        emit HouseholdManaged(household);
    }

    // ─────────────────────────────────────────────────────────────
    //  Optimierungs-Logik  (HIER IMPLEMENTIEREN TEAMS)
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Trifft eine Lade-/Entladeentscheidung für einen Haushalt.
     *
     *  TODO (Teams):
     *    1. Lese aktuellen SoC via oracle.getLatestBatteryState(household)
     *    2. Lese Meter-Daten: production - consumption = surplus/deficit
     *    3. Lese Wetterdaten: hohe Strahlung = mehr PV erwartet
     *    4. Entscheidungsbeispiel (einfache Heuristik):
     *         - Wenn Überschuss > 0 UND SoC < 90%: CHARGE
     *         - Wenn Defizit > 0 UND SoC > 20%:    DISCHARGE
     *         - Sonst:                              IDLE
     *    5. Komplexere Strategie könnte Wetter berücksichtigen:
     *         - Wenn cloudCover > 80%: Batterie entladen statt einspeisen
     *           (weil morgen weniger PV erwartet)
     *    6. Emit DecisionMade mit reason-String für Transparenz
     */
    /// @notice Ziel-Ladegrenze in Prozent. Darueber wird nicht mehr geladen,
    ///         um Reserve fuer die Einspeisung zu lassen.
    uint256 public constant MAX_SOC = 50;

    /// @notice Untere Entladegrenze in Prozent (Tiefentladeschutz).
    uint256 public constant MIN_SOC = 20;

    /// @notice Erhoehte Untergrenze bei starker Bewoelkung: dann ist im
    ///         naechsten Tagesabschnitt wenig PV zu erwarten, also wird mehr
    ///         Reserve im Speicher gehalten.
    uint256 public constant MIN_SOC_BAD_WEATHER = 40;

    /// @notice Ab dieser Bewoelkung (%) gilt die erhoehte Untergrenze.
    uint256 public constant CLOUD_THRESHOLD = 70;

    /**
     * @notice Trifft eine Lade-/Entladeentscheidung fuer einen Haushalt.
     *
     * @dev Heuristik:
     *        Ueberschuss + Platz im Speicher -> CHARGE
     *        Defizit + genug Ladung          -> DISCHARGE
     *        sonst                           -> IDLE
     *
     *      Die Menge ist immer dreifach begrenzt: durch den tatsaechlichen
     *      Ueberschuss bzw. das Defizit, durch die Lade-/Entladerate pro Slot
     *      und durch den physisch verfuegbaren Platz bzw. Energieinhalt.
     *      Die erste Grenze ist wichtig, weil P2PEnergyMarket den Betrag vom
     *      Netto abzieht bzw. dazuzaehlt - ohne sie koennte ein Produzent
     *      durch die Batterie-Korrektur zum Konsumenten werden.
     *
     *      Wetter geht ueber die variable Untergrenze ein: bei viel Bewoelkung
     *      wird frueher aufgehoert zu entladen.
     */
    function decideAction(address household) external returns (Action, uint256) {
        require(isManaged[household], "Not managed");

        IOracleStorage.BatteryState memory bs = oracle.getLatestBatteryState(household);
        IOracleStorage.MeterReading memory mr = oracle.getLatestMeterReading(household);
        IOracleStorage.WeatherData memory wd = oracle.getLatestWeather();

        uint256 slot = oracle.getCurrentSlot();

        // Ohne Kapazitaet oder Rate gibt es nichts zu entscheiden.
        if (bs.capacityWh == 0 || bs.maxRateWh == 0) {
            return _record(household, Action.IDLE, 0, slot, "keine Batterie");
        }

        int256 netto = int256(mr.productionWh) - int256(mr.consumptionWh);

        uint256 minSoc = wd.cloudCover >= CLOUD_THRESHOLD
            ? MIN_SOC_BAD_WEATHER
            : MIN_SOC;

        if (netto > 0 && bs.socPercent < MAX_SOC) {
            uint256 headroomWh = ((MAX_SOC - bs.socPercent) * bs.capacityWh) / 100;
            uint256 amount = _min3(uint256(netto), bs.maxRateWh, headroomWh);

            if (amount == 0) {
                return _record(household, Action.IDLE, 0, slot, "kein Ladepotenzial");
            }
            return _record(household, Action.CHARGE, amount, slot, "Ueberschuss speichern");
        }

        if (netto < 0 && bs.socPercent > minSoc) {
            uint256 usableWh = ((bs.socPercent - minSoc) * bs.capacityWh) / 100;
            uint256 amount = _min3(uint256(-netto), bs.maxRateWh, usableWh);

            if (amount == 0) {
                return _record(household, Action.IDLE, 0, slot, "kein Entladepotenzial");
            }
            return _record(household, Action.DISCHARGE, amount, slot, "Defizit aus Speicher");
        }

        // netto == 0, Speicher voll, oder SoC unter der Untergrenze.
        return _record(household, Action.IDLE, 0, slot, "keine Aktion noetig");
    }

    /// @dev Schreibt die Entscheidung in den Storage und protokolliert sie.
    function _record(
        address household,
        Action action,
        uint256 amountWh,
        uint256 slot,
        string memory reason
    ) internal returns (Action, uint256) {
        lastDecision[household] = Decision({
            action: action,
            amountWh: amountWh,
            slot: slot,
            timestamp: block.timestamp
        });
        emit DecisionMade(household, action, amountWh, slot, reason);
        return (action, amountWh);
    }

    function _min3(uint256 a, uint256 b, uint256 c) internal pure returns (uint256) {
        uint256 m = a < b ? a : b;
        return m < c ? m : c;
    }

    // ─────────────────────────────────────────────────────────────
    //  View Functions
    // ─────────────────────────────────────────────────────────────

    function getLastDecision(address household) external view returns (Decision memory) {
        return lastDecision[household];
    }

    function getManagedHouseholds() external view returns (address[] memory) {
        return managedHouseholds;
    }
}
