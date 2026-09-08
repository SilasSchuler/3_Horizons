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
     *  TODO (Teams):
     *    1. Hole currentSlot vom Oracle und prüfe, dass er > lastSettledSlot ist
     *    2. Iteriere über alle households:
     *       - Lese MeterReading via oracle.getLatestMeterReading()
     *       - Berechne netto (production - consumption)
     *       - [Phase 2, optional] Falls batteryManager gesetzt ist (siehe Feld
     *         oben + setBatteryManager()): passt netto VOR der Klassifizierung
     *         in Produzent/Konsument an, damit Handel und Batterie-Strategie
     *         konsistent sind, statt parallel und widersprüchlich zu laufen:
     *
     *           if (address(batteryManager) != address(0)
     *               && batteryManager.isManaged(household)) {
     *               try batteryManager.decideAction(household)
     *                   returns (IBatteryManager.Action action, uint256 amountWh) {
     *                   if (action == IBatteryManager.Action.CHARGE) {
     *                       netto -= int256(amountWh);   // Haushalt behält Energie für Batterie
     *                   } else if (action == IBatteryManager.Action.DISCHARGE) {
     *                       netto += int256(amountWh);   // Batterie liefert zusätzlich Energie
     *                   }
     *               } catch {
     *                   // Batterie-Call fehlgeschlagen -> ignorieren, Handel läuft
     *                   // ungestört mit dem ursprünglichen netto weiter
     *               }
     *           }
     *
     *         Wichtig: nicht jeder Haushalt hat zwingend eine verwaltete Batterie
     *         (z.B. reine Konsumenten) - deshalb zuerst isManaged() prüfen, sonst
     *         revertet decideAction() und blockiert den ganzen Slot für alle.
     *         decideAction() wird hier bewusst live innerhalb derselben Transaktion
     *         aufgerufen (nicht vorher separat getriggert) - so ist garantiert, dass
     *         die Entscheidung zum selben Slot gehört wie die Meter-Daten, die ihr
     *         gerade handelt, statt eine veraltete Entscheidung vom Vor-Slot zu lesen.
     *       - Sammle Überschüsse und Defizite
     *    3. Matche Produzenten mit Konsumenten
     *       (einfache Strategie: proportional verteilen)
     *    4. Pro Match: berechne Betrag = energieWh * effektiverPreisProKwh / 1000
     *       (Wattstunden -> Kilowattstunden)
     *       - [Phase 3, optional] Falls incentiveController gesetzt ist (siehe
     *         Feld oben + setIncentiveController()): passt den Preis für den
     *         KONSUMENTEN (Käufer) an, bevor ihr den Betrag berechnet:
     *
     *           uint256 pricePerKwh = energyPricePerKwh;
     *           if (address(incentiveController) != address(0)) {
     *               uint256 multiplier = incentiveController.getPriceMultiplier(consumer);
     *               pricePerKwh = (energyPricePerKwh * multiplier) / 1000;
     *           }
     *
     *         getPriceMultiplier() ist `view` (kein State-Change) - anders als
     *         batteryManager.decideAction() oben braucht ihr hier kein try/catch,
     *         der Call kann nicht versehentlich Storage kaputt machen.
     *         Multiplikator gilt bewusst für den Konsumenten, nicht den Produzenten
     *         (siehe IncentiveController.getPriceMultiplier(): 1000=neutral,
     *         <1000=Rabatt, >1000=Aufschlag - abhängig von dessen eigener
     *         Prognose-Genauigkeit als "Käufer").
     *    5. Transferiere via stablecoin.transferFrom(consumer, producer, amount)
     *       (Konsumenten müssen vorher approve() aufgerufen haben!)
     *    6. Emit EnergyTraded für jeden Match
     *    7. Setze lastSettledSlot auf currentSlot
     *    8. Emit SlotSettled
     */
    function settleSlot() external {
        // TODO: Implementierung durch Team

        revert("Not implemented yet - this is your job!");
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
