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
    /// @notice Konsument konnte nicht zahlen (kein approve() oder zu wenig Guthaben).
    /// @dev Bewusst Event statt revert: sonst blockiert ein Haushalt den ganzen Slot.
    event ConsumerSkipped(address indexed consumer, uint256 slot, uint256 requestedAmount);
    // ─────────────────────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }
    
    /// @dev Reentrancy-Sperre. settleSlot() ruft mit transferFrom() einen externen
    ///      Token-Contract auf. Ohne Sperre koennte ein boesartiger Token von dort
    ///      aus erneut settleSlot() aufrufen und denselben Slot doppelt abrechnen.
    bool private _locked;

    modifier nonReentrant() {
        require(!_locked, "Reentrant call");
        _locked = true;
        _;
        _locked = false;
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
     * @notice Repräsentiert die Position eines Haushalts im aktuellen Slot.
     * @dev surplus = überschüssige Energie, deficit = fehlende Energie
     */
    struct Position {
        address household;
        uint256 surplus;
        uint256 deficit;
    }

    struct Trade{
        address producer;
        address consumer;
        uint256 energyWh;
    }

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
function settleSlot() external nonReentrant {

    // 1. Hole currentSlot vom Oracle und prüfe, dass er > lastSettledSlot ist

    uint256 currentSlot = oracle.getCurrentSlot();
    require(currentSlot > lastSettledSlot, "Current slot must be greater than last settled slot");

    // 2. Iteriere über alle households:
    //    - Lese MeterReading via oracle.getLatestMeterReading()
    //    - Sammle Überschüsse und Defizite

    Position[] memory positions = new Position[](households.length);

    for (uint256 i = 0; i < households.length; i++) {
        IOracleStorage.MeterReading memory reading = oracle.getLatestMeterReading(households[i]);

        // Needs to be signed to distinguish between surplus and deficit
        int256 netto = int256(reading.productionWh) - int256(reading.consumptionWh);

        uint256 surplus = 0;
        uint256 deficit = 0;
        if (netto > 0) {
            surplus = uint256(netto);
        } else {
            deficit = uint256(-netto);
        }

        positions[i] = Position({
            household: households[i],
            surplus: surplus,
            deficit: deficit
        });
    }

    // 3. Matche Produzenten mit Konsumenten
    // (einfache Strategie: proportional verteilen)

    uint256 totalSurplus = 0;
    uint256 totalDeficit = 0;
    for (uint256 i = 0; i < positions.length; i++) {
        totalSurplus += positions[i].surplus;
        totalDeficit += positions[i].deficit;
    }

    // If negative: more deficit than surplus -> scale deficits down.
    // If positive: more surplus than deficit -> scale surpluses down.
    int256 totalDistributed = int256(totalSurplus) - int256(totalDeficit);

    if (totalDistributed < 0 && totalDeficit > 0) {
        // Not enough surplus to cover all deficits -> scale each deficit down proportionally
        for (uint256 i = 0; i < positions.length; i++) {
            positions[i].deficit = (positions[i].deficit * totalSurplus) / totalDeficit;
        }
    } else if (totalDistributed > 0 && totalSurplus > 0) {
        // Not enough deficit to absorb all surplus -> scale each surplus down proportionally
        for (uint256 i = 0; i < positions.length; i++) {
            positions[i].surplus = (positions[i].surplus * totalDeficit) / totalSurplus;
        }
    }
    // If totalDistributed == 0, surplus and deficit already balance exactly -> no scaling needed.

    // 4. Pro Match: berechne Betrag = energieWh * effektiverPreisProKwh / 1000
    //  *       (Wattstunden -> Kilowattstunden)

    
    //Assumption. With each trade either a producer or consumer is used up. So there never should be more than double the total number of them.
    uint256 producerIndex = 0;
    uint256 tradeCount = 0;
    Trade[] memory trades = new Trade[](households.length); // see sizing note below

    for (uint256 i = 0; i < positions.length; i++) {
        uint256 remainingDeficit = positions[i].deficit;

        while (remainingDeficit > 0) {
            // If there are comma errors the last consumer might end up with a slightly higher deficit.
            while (producerIndex < positions.length && positions[producerIndex].surplus == 0) {
                producerIndex++;
            }
            if (producerIndex >= positions.length) {
                break; // no producers left; any leftover remainingDeficit goes unfilled
            }
            uint256 matched = min(remainingDeficit, positions[producerIndex].surplus);

            trades[tradeCount] = Trade({
                producer: positions[producerIndex].household,
                consumer: positions[i].household,
                energyWh: matched
            });
            tradeCount++;

            remainingDeficit -= matched;
            positions[producerIndex].surplus -= matched;
        }
    }

    //  *    5. Transferiere via stablecoin.transferFrom(consumer, producer, amount)
    //  *       (Konsumenten müssen vorher approve() aufgerufen haben!)
    uint256 totalEnergyTraded = 0;
    uint256 totalPaid = 0;

    // 7. Setze lastSettledSlot auf currentSlot
    // Checks-Effects-Interactions: Status VOR den externen Token-Calls setzen.
    // Zusammen mit nonReentrant ausgeschlossen, dass ein reentranter Aufruf
    // denselben Slot doppelt abrechnet.
    lastSettledSlot = currentSlot;
    lastSettledSlot = currentSlot;

    for (uint256 i = 0; i < tradeCount; i++) {
        uint256 calculatedCost = calculateCost(trades[i].energyWh);

        // try/catch faengt den Revert ab, die bool-Pruefung faengt Token,
        // die bei fehlender Allowance false zurueckgeben statt zu reverten.
        bool paid;
        try stablecoin.transferFrom(trades[i].consumer, trades[i].producer, calculatedCost)
            returns (bool ok)
        {
            paid = ok;
        } catch {
            paid = false;
        }

        if (!paid) {
            emit ConsumerSkipped(trades[i].consumer, currentSlot, calculatedCost);
            continue;
        }

        emit EnergyTraded(trades[i].producer, trades[i].consumer, trades[i].energyWh, calculatedCost, currentSlot);

        totalEnergyTraded += trades[i].energyWh;
        totalPaid += calculatedCost;
    }



    // 8. Emit SlotSettled
    emit SlotSettled(currentSlot, totalEnergyTraded, totalPaid);

    //ToDo: If there is a surplus that should be payed to producers. Scaled the same way with constant rate for Network.

}

 

    function min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
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
