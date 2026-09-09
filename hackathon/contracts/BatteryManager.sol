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

    /// @notice Obere SoC-Schwelle: darüber wird nicht mehr geladen.
    uint256 public constant MAX_CHARGE_SOC = 90;

    /// @notice Untere SoC-Schwelle für Entladung im Normalfall.
    uint256 public constant MIN_DISCHARGE_SOC = 20;

    /// @notice Strengere untere SoC-Schwelle bei hoher Bewölkung: die Batterie
    ///         wird stärker geschont, weil kurzfristig mit wenig PV-Nachschub
    ///         zu rechnen ist.
    uint256 public constant MIN_DISCHARGE_SOC_CLOUDY = 40;

    /// @notice Bewölkungsgrad (%), ab dem die strengere Reserve-Schwelle gilt.
    uint256 public constant CLOUD_COVER_THRESHOLD = 80;

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
     * @dev Heuristik:
     *        - Überschuss (Produktion > Verbrauch) UND SoC < MAX_CHARGE_SOC:
     *          CHARGE mit min(Überschuss, maxRateWh, freie Kapazität bis zur
     *          Schwelle).
     *        - Defizit (Verbrauch > Produktion) UND SoC über der Reserve-
     *          Schwelle: DISCHARGE mit min(Defizit, maxRateWh, verfügbare
     *          Kapazität oberhalb der Reserve).
     *          Die Reserve-Schwelle ist bei hoher Bewölkung (> CLOUD_COVER_THRESHOLD)
     *          höher (MIN_DISCHARGE_SOC_CLOUDY statt MIN_DISCHARGE_SOC), weil
     *          dann kurzfristig weniger PV-Nachschub zu erwarten ist und die
     *          Batterie stärker geschont werden soll.
     *        - Sonst (kein Netto, kein SoC-Spielraum, keine Batterie
     *          vorhanden): IDLE.
     *      `amountWh` ist auf `maxRateWh` (Lade-/Entladerate pro Slot) und die
     *      tatsächlich verfügbare Kapazität begrenzt, damit die Entscheidung
     *      physikalisch plausibel bleibt.
     */
    function decideAction(address household) external returns (Action, uint256) {
        require(isManaged[household], "Not managed");

        IOracleStorage.BatteryState memory bs = oracle.getLatestBatteryState(household);
        IOracleStorage.MeterReading memory mr = oracle.getLatestMeterReading(household);
        IOracleStorage.WeatherData memory wd = oracle.getLatestWeather();

        Action action = Action.IDLE;
        uint256 amountWh;
        string memory reason = "no-battery-capacity";

        if (bs.capacityWh > 0) {
            if (mr.productionWh > mr.consumptionWh) {
                uint256 surplusWh = mr.productionWh - mr.consumptionWh;

                if (bs.socPercent < MAX_CHARGE_SOC) {
                    uint256 roomWh = (bs.capacityWh * (MAX_CHARGE_SOC - bs.socPercent)) / 100;
                    amountWh = _min(surplusWh, _min(bs.maxRateWh, roomWh));
                    if (amountWh > 0) {
                        action = Action.CHARGE;
                        reason = "surplus-charge";
                    } else {
                        reason = "surplus-but-no-room";
                    }
                } else {
                    reason = "soc-at-max-charge-threshold";
                }
            } else if (mr.consumptionWh > mr.productionWh) {
                uint256 deficitWh = mr.consumptionWh - mr.productionWh;
                bool cloudy = wd.cloudCover > CLOUD_COVER_THRESHOLD;
                uint256 minSoc = cloudy ? MIN_DISCHARGE_SOC_CLOUDY : MIN_DISCHARGE_SOC;

                if (bs.socPercent > minSoc) {
                    uint256 availableWh = (bs.capacityWh * (bs.socPercent - minSoc)) / 100;
                    amountWh = _min(deficitWh, _min(bs.maxRateWh, availableWh));
                    if (amountWh > 0) {
                        action = Action.DISCHARGE;
                        reason = cloudy ? "deficit-discharge-cloudy-reserve" : "deficit-discharge";
                    } else {
                        reason = "deficit-but-below-reserve";
                    }
                } else {
                    reason = "soc-below-discharge-threshold";
                }
            } else {
                reason = "balanced-no-action";
            }
        }

        uint256 slot = oracle.getCurrentSlot();

        lastDecision[household] = Decision({
            action: action,
            amountWh: amountWh,
            slot: slot,
            timestamp: block.timestamp
        });

        emit DecisionMade(household, action, amountWh, slot, reason);

        return (action, amountWh);
    }

    /// @dev Kleinere von zwei uint256-Zahlen.
    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
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
