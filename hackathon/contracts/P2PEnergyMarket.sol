// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IEnergyStablecoin.sol";
import "./interfaces/IOracleStorage.sol";
import "./interfaces/IBatteryManager.sol";
import "./interfaces/IIncentiveController.sol";

/**
 * @title P2PEnergyMarket
 * @notice Phase 1: Direkter Energiehandel zwischen Haushalten.
 * @dev STARTER-CODE - Teams implementieren die TODO-Blöcke.
 *
 *      Logik (Beispiel-Vorschlag):
 *        1. Pro Slot: Lese alle Meter-Daten aus dem Oracle
 *        2. Berechne pro Haushalt: Überschuss = produktion - verbrauch
 *        3. Matche Produzenten (Überschuss > 0) mit Konsumenten (Defizit)
 *        4. Transferiere Stablecoin von Konsument an Produzent
 *
 *      Wichtig: Konsumenten müssen vorab approve() auf den Stablecoin aufrufen,
 *               damit der Contract Tokens in ihrem Namen transferieren kann.
 */
contract P2PEnergyMarket {

    // ─────────────────────────────────────────────────────────────
    //  Storage
    // ─────────────────────────────────────────────────────────────

    IEnergyStablecoin public immutable stablecoin;
    IOracleStorage public immutable oracle;

    address public owner;
    address[] public households;
    mapping(address => bool) public isRegistered;

    /// @notice Energiepreis in Token-Einheiten pro kWh (6 Decimals).
    /// @dev Beispiel: 100_000 = 0.10 Token / kWh
    uint256 public energyPricePerKwh = 100_000;

    /// @notice Letzter abgerechneter Slot, um Doppelabrechnung zu verhindern
    uint256 public lastSettledSlot;

    /// @notice Optionale Phase-2-Integration: BatteryManager-Contract.
    /// @dev Default = address(0) -> keine Batterie-Logik aktiv, settleSlot()
    ///      verhält sich dann wie in Phase 1 (reiner Meter-Nettowert wird
    ///      gehandelt). Setzen via setBatteryManager() NACH dem Deployment
    ///      (Reihenfolge laut README bleibt: OracleStorage -> P2PEnergyMarket
    ///      -> BatteryManager, danach hier eintragen - deshalb Setter statt
    ///      Constructor-Arg, sonst würde die Deploy-Reihenfolge kollidieren).
    IBatteryManager public batteryManager;

    /// @notice Optionale Phase-3-Integration: IncentiveController-Contract.
    /// @dev Default = address(0) -> kein Preis-Incentive aktiv, settleSlot()
    ///      nutzt dann den unveränderten energyPricePerKwh (wie in Phase 1/2).
    ///      Setzen via setIncentiveController() NACH dem Deployment (Reihenfolge
    ///      laut README: ... -> IncentiveController, danach hier eintragen).
    IIncentiveController public incentiveController;

    // ─────────────────────────────────────────────────────────────
    //  Events
    // ─────────────────────────────────────────────────────────────

    event HouseholdRegistered(address indexed household);
    event EnergyTraded(
        address indexed producer,
        address indexed consumer,
        uint256 energyWh,
        uint256 amountPaid,
        uint256 slot
    );
    event SlotSettled(uint256 indexed slot, uint256 totalEnergyTraded, uint256 totalPaid);
    event PriceUpdated(uint256 newPricePerKwh);
    event BatteryManagerUpdated(address indexed batteryManager);
    event IncentiveControllerUpdated(address indexed incentiveController);

    // ─────────────────────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    // ─────────────────────────────────────────────────────────────
    //  Constructor
    // ─────────────────────────────────────────────────────────────

    /**
     * @param _stablecoin Adresse des bereitgestellten ERC-20 Stablecoins
     * @param _oracle Adresse des OracleStorage Contracts
     */
    constructor(address _stablecoin, address _oracle) {
        stablecoin = IEnergyStablecoin(_stablecoin);
        oracle = IOracleStorage(_oracle);
        owner = msg.sender;
    }

    // ─────────────────────────────────────────────────────────────
    //  Registrierung
    // ─────────────────────────────────────────────────────────────

    /// @notice Registriert einen Haushalt für den Marktplatz
    function registerHousehold(address household) external onlyOwner {
        require(!isRegistered[household], "Already registered");
        require(oracle.isHouseholdRegistered(household), "Not in oracle");
        households.push(household);
        isRegistered[household] = true;
        emit HouseholdRegistered(household);
    }

    function setEnergyPrice(uint256 newPricePerKwh) external onlyOwner {
        energyPricePerKwh = newPricePerKwh;
        emit PriceUpdated(newPricePerKwh);
    }

    /// @notice Verknüpft optional den BatteryManager-Contract (Phase 2).
    /// @dev address(0) = deaktiviert (Standard) -> settleSlot() verhält sich
    ///      wie in Phase 1. Erst NACH dem Deployment von BatteryManager aufrufen.
    function setBatteryManager(address _batteryManager) external onlyOwner {
        batteryManager = IBatteryManager(_batteryManager);
        emit BatteryManagerUpdated(_batteryManager);
    }

    /// @notice Verknüpft optional den IncentiveController-Contract (Phase 3).
    /// @dev address(0) = deaktiviert (Standard) -> settleSlot() nutzt den
    ///      unveränderten energyPricePerKwh. Erst NACH dem Deployment von
    ///      IncentiveController aufrufen.
    function setIncentiveController(address _incentiveController) external onlyOwner {
        incentiveController = IIncentiveController(_incentiveController);
        emit IncentiveControllerUpdated(_incentiveController);
    }

    // ─────────────────────────────────────────────────────────────
    //  Settlement-Logik  (HIER IMPLEMENTIEREN TEAMS)
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Rechnet einen Slot ab: matched Produzenten mit Konsumenten,
     *         transferiert Stablecoin entsprechend.
     *
     * @dev Matching-Strategie: sequenzielles Draining statt proportionaler
     *      Verteilung. Produzenten und Konsumenten werden je nach Netto
     *      (production - consumption, ggf. durch BatteryManager angepasst)
     *      in zwei Listen sortiert und mit zwei Zeigern gegeneinander
     *      abgearbeitet - der jeweils kleinere Rest (Überschuss/Defizit)
     *      wird komplett gehandelt, bevor zum nächsten Produzenten/Konsumenten
     *      weitergegangen wird. Das erzeugt maximal (producerCount + consumerCount)
     *      Matches/Events statt O(n²) bei voller proportionaler Aufteilung,
     *      bleibt aber vollständig (alle Überschüsse/Defizite werden geräumt,
     *      solange Gesamtangebot und -nachfrage übereinstimmen).
     */
    function settleSlot() external {
        uint256 slot = oracle.getCurrentSlot();
        require(slot > lastSettledSlot, "Slot already settled");

        uint256 n = households.length;
        address[] memory producers = new address[](n);
        uint256[] memory surplus = new uint256[](n);
        uint256 producerCount;

        address[] memory consumers = new address[](n);
        uint256[] memory deficit = new uint256[](n);
        uint256 consumerCount;

        for (uint256 i = 0; i < n; i++) {
            address household = households[i];
            IOracleStorage.MeterReading memory reading = oracle.getLatestMeterReading(household);
            int256 net = int256(reading.productionWh) - int256(reading.consumptionWh);

            if (address(batteryManager) != address(0) && batteryManager.isManaged(household)) {
                try batteryManager.decideAction(household)
                    returns (IBatteryManager.Action action, uint256 amountWh)
                {
                    if (action == IBatteryManager.Action.CHARGE) {
                        net -= int256(amountWh);
                    } else if (action == IBatteryManager.Action.DISCHARGE) {
                        net += int256(amountWh);
                    }
                } catch {
                    // Batterie-Call fehlgeschlagen -> ignorieren, mit ursprünglichem netto weiter
                }
            }

            if (net > 0) {
                producers[producerCount] = household;
                surplus[producerCount] = uint256(net);
                producerCount++;
            } else if (net < 0) {
                consumers[consumerCount] = household;
                deficit[consumerCount] = uint256(-net);
                consumerCount++;
            }
        }

        uint256 totalEnergyTraded;
        uint256 totalPaid;
        uint256 p;
        uint256 c;

        while (p < producerCount && c < consumerCount) {
            address producer = producers[p];
            address consumer = consumers[c];
            uint256 tradeWh = surplus[p] < deficit[c] ? surplus[p] : deficit[c];

            if (tradeWh > 0) {
                uint256 pricePerKwh = energyPricePerKwh;
                if (address(incentiveController) != address(0)) {
                    uint256 multiplier = incentiveController.getPriceMultiplier(consumer);
                    pricePerKwh = (energyPricePerKwh * multiplier) / 1000;
                }

                uint256 amount = (tradeWh * pricePerKwh) / 1000;

                bool success = stablecoin.transferFrom(consumer, producer, amount);
                require(success, "Token transfer failed");

                emit EnergyTraded(producer, consumer, tradeWh, amount, slot);

                totalEnergyTraded += tradeWh;
                totalPaid += amount;

                surplus[p] -= tradeWh;
                deficit[c] -= tradeWh;
            }

            if (surplus[p] == 0) p++;
            if (deficit[c] == 0) c++;
        }

        lastSettledSlot = slot;
        emit SlotSettled(slot, totalEnergyTraded, totalPaid);
    }

    // ─────────────────────────────────────────────────────────────
    //  View Functions (Hilfsfunktionen)
    // ─────────────────────────────────────────────────────────────

    function getHouseholdCount() external view returns (uint256) {
        return households.length;
    }

    function getAllHouseholds() external view returns (address[] memory) {
        return households;
    }

    /// @notice Helper: Berechnet den Token-Betrag für eine Energiemenge
    function calculateCost(uint256 energyWh) public view returns (uint256) {
        // Wh -> kWh -> Token (mit Decimals)
        return (energyWh * energyPricePerKwh) / 1000;
    }
}
