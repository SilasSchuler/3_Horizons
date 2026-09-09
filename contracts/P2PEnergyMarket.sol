// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IEnergyStablecoin.sol";
import "./interfaces/IOracleStorage.sol";
import "./interfaces/IBatteryManager.sol";
import "./interfaces/IIncentiveController.sol";

/**
 * @title P2PEnergyMarket
 * @notice Phase 1: Direkter Energiehandel zwischen Haushalten.
 *
 * @dev Ablauf von settleSlot() in Kurzform:
 *
 *        1. Slot vom Oracle holen und gegen lastSettledSlot prüfen
 *        2. Pro Haushalt Meter-Daten lesen -> netto = Produktion - Verbrauch
 *           (optional durch BatteryManager korrigiert, Phase 2)
 *        3. Netto > 0 -> Produzent, netto < 0 -> Konsument
 *        4. Beide Seiten proportional auf die handelbare Menge skalieren
 *        5. Produzenten und Konsumenten mit Zwei-Zeiger-Verfahren matchen
 *        6. Pro Match: Preis (optional mit Incentive-Multiplikator, Phase 3),
 *           Betrag berechnen und Stablecoin per transferFrom() verschieben
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

    /// @dev Reentrancy-Sperre. settleSlot() ruft mit transferFrom() einen
    ///      externen Token-Contract auf. Ohne Sperre könnte ein bösartiger
    ///      Token aus transferFrom() heraus erneut settleSlot() aufrufen und
    ///      denselben Slot mehrfach abrechnen.
    bool private _locked;

    // ─────────────────────────────────────────────────────────────
    //  Datenstrukturen für das Matching (nur memory, kein Storage)
    // ─────────────────────────────────────────────────────────────

    /// @dev Eine offene Position eines Haushalts innerhalb eines Slots.
    ///      amountWh ist immer positiv - die Richtung ergibt sich daraus,
    ///      in welcher Liste die Position steht (producers oder consumers).
    struct Position {
        address account;
        uint256 amountWh;
    }

    /// @dev Sammelergebnis eines Slots. Wird als ein einziges memory-Objekt
    ///      zwischen den internen Funktionen weitergereicht, damit wir nicht
    ///      in "stack too deep" laufen.
    ///      producers/consumers sind auf households.length dimensioniert,
    ///      tatsächlich befüllt sind nur die ersten producerCount bzw.
    ///      consumerCount Einträge.
    struct SlotPositions {
        Position[] producers;
        Position[] consumers;
        uint256 producerCount;
        uint256 consumerCount;
        uint256 totalSurplus;
        uint256 totalDeficit;
    }

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

    /// @notice Ein Konsument konnte nicht zahlen (fehlendes approve() oder
    ///         zu wenig Guthaben) und wurde für diesen Slot übersprungen.
    /// @dev Bewusst als Event statt als revert: sonst blockiert ein einziger
    ///      zahlungsunfähiger Haushalt die Abrechnung für alle anderen.
    event ConsumerSkipped(address indexed consumer, uint256 slot, uint256 requestedAmount);

    /// @notice Der BatteryManager-Call ist fehlgeschlagen, es wurde mit dem
    ///         unkorrigierten Meter-Netto weitergerechnet.
    event BatteryDecisionFailed(address indexed household, uint256 slot);

    // ─────────────────────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

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
    //  Settlement-Logik
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Rechnet einen Slot ab: matched Produzenten mit Konsumenten und
     *         transferiert Stablecoin entsprechend.
     *
     * @dev Schritt 1 und 7/8 der Aufgabenstellung liegen hier, die Schritte
     *      2-6 sind in _collectPositions() und _matchAndTrade() ausgelagert.
     *      Grund für die Aufteilung: settleSlot() als eine einzige Funktion
     *      läuft mit den vielen lokalen Variablen schnell in "stack too deep".
     *
     *      Die Funktion ist bewusst permissionless (jeder darf sie aufrufen) -
     *      der settlement_trigger.py braucht dafür keinen Owner-Key. Gegen
     *      Missbrauch schützt die lastSettledSlot-Prüfung: ein Slot kann nur
     *      einmal abgerechnet werden.
     */
    function settleSlot() external nonReentrant {
        // ── Schritt 1: Slot holen und Doppelabrechnung verhindern ──────
        // Hinweis: hier steht bewusst ">" und nicht ">=" wie im Starter-Code.
        // Mit ">=" liesse sich derselbe Slot beliebig oft abrechnen, weil
        // currentSlot == lastSettledSlot die Prüfung passieren würde.
        uint256 currentSlot = oracle.getCurrentSlot();
        require(currentSlot > lastSettledSlot, "Slot already settled");

        // ── Schritte 2 + 3: Positionen einsammeln und klassifizieren ───
        SlotPositions memory pos = _collectPositions(currentSlot);

        // ── Schritt 7 (vorgezogen): Checks-Effects-Interactions ────────
        // lastSettledSlot wird VOR den Token-Transfers gesetzt. Zusammen mit
        // nonReentrant ist damit ausgeschlossen, dass ein reentranter Call
        // denselben Slot ein zweites Mal abrechnet.
        lastSettledSlot = currentSlot;

        // ── Schritte 4, 5, 6: Matching, Preis und Transfers ────────────
        (uint256 totalEnergyTraded, uint256 totalPaid) = _matchAndTrade(pos, currentSlot);

        // ── Schritt 8: Slot-Abschluss protokollieren ───────────────────
        emit SlotSettled(currentSlot, totalEnergyTraded, totalPaid);
    }

    /**
     * @dev Schritt 2 + 3: Liest für jeden registrierten Haushalt die Meter-Daten,
     *      berechnet das Netto und sortiert den Haushalt in Produzenten oder
     *      Konsumenten ein.
     *
     *      Nicht `view`, weil batteryManager.decideAction() den State des
     *      BatteryManagers verändern darf.
     *
     * @param currentSlot Nur für Event-Logging bei fehlgeschlagenem Batterie-Call.
     */
    function _collectPositions(uint256 currentSlot)
        internal
        returns (SlotPositions memory pos)
    {
        uint256 n = households.length;

        // Worst Case: alle Haushalte landen auf derselben Seite. Deshalb beide
        // Arrays auf volle Länge dimensionieren; memory-Arrays haben in
        // Solidity eine feste Grösse und lassen sich nicht per push() erweitern.
        pos.producers = new Position[](n);
        pos.consumers = new Position[](n);

        for (uint256 i = 0; i < n; i++) {
            address household = households[i];

            // Meter-Daten des Haushalts für den aktuellen Slot lesen.
            IOracleStorage.MeterReading memory r = oracle.getLatestMeterReading(household);

            // Netto in Wh: positiv = Überschuss, negativ = Defizit.
            // int256 ist nötig, weil uint256 bei Verbrauch > Produktion
            // in einen Underflow-Revert laufen würde.
            int256 netto = int256(r.productionWh) - int256(r.consumptionWh);

            // ── Phase 2 (optional): Batterie-Entscheidung einrechnen ────
            // Passiert VOR der Klassifizierung, damit Handel und Batterie-
            // Strategie konsistent sind statt parallel und widersprüchlich.
            netto = _applyBatteryDecision(household, netto, currentSlot);

            // ── Schritt 3: Klassifizierung ──────────────────────────────
            if (netto > 0) {
                uint256 surplus = uint256(netto);
                pos.producers[pos.producerCount] = Position(household, surplus);
                pos.producerCount++;
                pos.totalSurplus += surplus;
            } else if (netto < 0) {
                uint256 deficit = uint256(-netto);
                pos.consumers[pos.consumerCount] = Position(household, deficit);
                pos.consumerCount++;
                pos.totalDeficit += deficit;
            }
            // netto == 0: ausgeglichener Haushalt, nimmt am Handel nicht teil.
        }
    }

    /**
     * @dev Phase 2 (optional): Korrigiert das Meter-Netto um die Entscheidung
     *      des BatteryManagers.
     *
     *      CHARGE    -> der Haushalt behält Energie für die Batterie,
     *                   es steht weniger zum Verkauf bereit  -> netto sinkt
     *      DISCHARGE -> die Batterie liefert zusätzliche Energie -> netto steigt
     *
     *      Zwei Absicherungen:
     *        - isManaged() zuerst prüfen: nicht jeder Haushalt hat eine
     *          verwaltete Batterie (z.B. reine Konsumenten). Ohne die Prüfung
     *          würde decideAction() reverten und den ganzen Slot für alle
     *          blockieren.
     *        - try/catch: schlägt der Call trotzdem fehl, rechnen wir mit dem
     *          unkorrigierten Netto weiter, statt die Abrechnung abzubrechen.
     *
     *      decideAction() wird bewusst live in derselben Transaktion aufgerufen
     *      und nicht vorher separat getriggert - so gehört die Entscheidung
     *      garantiert zum selben Slot wie die Meter-Daten, die gerade gehandelt
     *      werden, statt zu einer veralteten Entscheidung vom Vor-Slot.
     */
    function _applyBatteryDecision(address household, int256 netto, uint256 currentSlot)
        internal
        returns (int256)
    {
        // Nicht verknüpft (Phase 1) -> unverändert zurück, kein externer Call.
        if (address(batteryManager) == address(0)) {
            return netto;
        }
        if (!batteryManager.isManaged(household)) {
            return netto;
        }

        try batteryManager.decideAction(household)
            returns (IBatteryManager.Action action, uint256 amountWh)
        {
            if (action == IBatteryManager.Action.CHARGE) {
                netto -= int256(amountWh);
            } else if (action == IBatteryManager.Action.DISCHARGE) {
                netto += int256(amountWh);
            }
            // Alle anderen Actions (z.B. IDLE): keine Korrektur.
        } catch {
            // Batterie-Call fehlgeschlagen -> ignorieren, Handel läuft
            // ungestört mit dem ursprünglichen netto weiter.
            emit BatteryDecisionFailed(household, currentSlot);
        }

        return netto;
    }

    /**
     * @dev Schritte 4, 5 und 6: skaliert beide Marktseiten auf die handelbare
     *      Menge, matched sie und führt die Token-Transfers aus.
     *
     *      Matching-Strategie (zweistufig):
     *
     *        a) Proportionale Skalierung
     *           Angebot und Nachfrage sind praktisch nie gleich gross. Die
     *           grössere Seite wird deshalb proportional heruntergerechnet,
     *           bis beide Seiten auf tradable = min(Angebot, Nachfrage) passen.
     *           Damit trägt jeder Teilnehmer denselben relativen Anteil der
     *           Knappheit - niemand wird nur deshalb voll bedient, weil er
     *           zufällig weit vorne im households-Array steht.
     *
     *        b) Zwei-Zeiger-Matching
     *           Danach werden beide Listen mit je einem Zeiger durchlaufen und
     *           immer min(Restangebot, Restnachfrage) gehandelt. Das erzeugt
     *           höchstens producerCount + consumerCount - 1 Transfers statt
     *           producerCount * consumerCount bei einem vollen Kreuzprodukt -
     *           relevant für das Gas-Limit (siehe README-Hinweis).
     *
     *      Rundung: die Ganzzahldivision bei der Skalierung rundet ab, die
     *      skalierte Seite ist also minimal kleiner als tradable. Das ist
     *      unkritisch, weil jeder Match ohnehin das Minimum beider Seiten
     *      nimmt - es bleibt höchstens ein Rest von wenigen Wh ungehandelt.
     */
    function _matchAndTrade(SlotPositions memory pos, uint256 currentSlot)
        internal
        returns (uint256 totalEnergyTraded, uint256 totalPaid)
    {
        // ── Schritt 4a: handelbare Menge und proportionale Skalierung ──
        // Der Block ist bewusst gescoped: tradable wird nach der Skalierung
        // nicht mehr gebraucht und gibt seinen Stack-Slot wieder frei.
        {
            uint256 tradable = pos.totalSurplus < pos.totalDeficit
                ? pos.totalSurplus
                : pos.totalDeficit;

            // Nur eine Marktseite vorhanden (oder gar keine) -> nichts zu tun.
            // Der Slot gilt trotzdem als abgerechnet, damit die Abrechnung nicht
            // bei jedem Trigger erneut über dieselben Daten läuft.
            if (tradable == 0) {
                return (0, 0);
            }

            if (pos.totalSurplus > tradable) {
                // Überangebot: Produzenten können nur anteilig verkaufen.
                for (uint256 i = 0; i < pos.producerCount; i++) {
                    pos.producers[i].amountWh =
                        (pos.producers[i].amountWh * tradable) / pos.totalSurplus;
                }
            }
            if (pos.totalDeficit > tradable) {
                // Übernachfrage: Konsumenten bekommen nur anteilig geliefert.
                // Den Rest bezieht der Haushalt in der Realität aus dem Netz -
                // das liegt ausserhalb dieses Marktplatzes.
                for (uint256 j = 0; j < pos.consumerCount; j++) {
                    pos.consumers[j].amountWh =
                        (pos.consumers[j].amountWh * tradable) / pos.totalDeficit;
                }
            }
        }

        // ── Schritt 5: Zwei-Zeiger-Matching ────────────────────────────
        uint256 p = 0; // Zeiger auf den aktuellen Produzenten
        uint256 c = 0; // Zeiger auf den aktuellen Konsumenten

        while (p < pos.producerCount && c < pos.consumerCount) {
            // Vollständig bediente (oder durch Rundung leere) Position:
            // Zeiger weiterschieben.
            if (pos.producers[p].amountWh == 0) { p++; continue; }
            if (pos.consumers[c].amountWh == 0) { c++; continue; }

            // Der eigentliche Handel liegt in _executeMatch(). Die Auslagerung
            // ist nicht nur Kosmetik: mit producer, consumer, energyWh und
            // amount zusätzlich in dieser Schleife läuft der Compiler in
            // "stack too deep" (max. 16 erreichbare Stack-Slots).
            (uint256 energyWh, uint256 amount, bool ok) = _executeMatch(pos, p, c, currentSlot);

            if (ok) {
                totalEnergyTraded += energyWh;
                totalPaid += amount;
            } else {
                // Konsument konnte nicht zahlen und wurde in _executeMatch()
                // auf 0 gesetzt -> zum nächsten Konsumenten. Die Energie des
                // Produzenten bleibt für ihn verfügbar.
                c++;
            }
        }
    }

    /**
     * @dev Schritt 4b + 6: führt einen einzelnen Match aus - Menge bestimmen,
     *      Preis berechnen, Token transferieren, Event schreiben.
     *
     *      pos ist ein memory-Struct und wird per Referenz übergeben, die
     *      Reduktion der offenen Mengen wirkt also auch in _matchAndTrade().
     *
     * @return energyWh gehandelte Energiemenge
     * @return amount   gezahlter Token-Betrag
     * @return ok       false, wenn der Konsument nicht zahlen konnte
     */
    function _executeMatch(SlotPositions memory pos, uint256 p, uint256 c, uint256 currentSlot)
        internal
        returns (uint256 energyWh, uint256 amount, bool ok)
    {
        address producer = pos.producers[p].account;
        address consumer = pos.consumers[c].account;

        // Gehandelt wird immer das Minimum beider offenen Mengen.
        energyWh = pos.producers[p].amountWh < pos.consumers[c].amountWh
            ? pos.producers[p].amountWh
            : pos.consumers[c].amountWh;

        // Preis hängt am Konsumenten (siehe getEffectivePrice).
        amount = calculateCostFor(consumer, energyWh);

        // transferFrom() greift auf die Allowance zu, die der Konsument dem
        // Contract vorher per approve() erteilt haben muss.
        if (_trySettleTrade(consumer, producer, amount)) {
            pos.producers[p].amountWh -= energyWh;
            pos.consumers[c].amountWh -= energyWh;
            emit EnergyTraded(producer, consumer, energyWh, amount, currentSlot);
            ok = true;
        } else {
            // Kein approve(), zu wenig Guthaben oder Token-Revert: Konsument
            // wird für diesen Slot komplett übersprungen statt zu reverten.
            emit ConsumerSkipped(consumer, currentSlot, amount);
            pos.consumers[c].amountWh = 0;
            ok = false;
        }
    }

    /**
     * @dev Führt einen einzelnen Zahlungsvorgang aus und meldet Erfolg statt
     *      zu reverten.
     *
     *      Zwei Fehlerfälle werden abgefangen:
     *        - Der Token revertet (klassisches ERC-20-Verhalten bei zu
     *          geringer Allowance oder Balance) -> catch
     *        - Der Token gibt false zurück, statt zu reverten (ältere
     *          Token-Implementierungen) -> Rückgabewert prüfen
     */
    function _trySettleTrade(address consumer, address producer, uint256 amount)
        internal
        returns (bool)
    {
        // Betrag 0 (z.B. sehr kleine Energiemenge nach Rundung): kein Transfer
        // nötig, der Match gilt trotzdem als erfolgreich.
        if (amount == 0) {
            return true;
        }

        try stablecoin.transferFrom(consumer, producer, amount) returns (bool ok) {
            return ok;
        } catch {
            return false;
        }
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

    /**
     * @notice Effektiver Preis pro kWh für einen bestimmten Konsumenten.
     * @dev Phase 3 (optional): Ist der IncentiveController verknüpft, wird der
     *      Basispreis mit dem Multiplikator des Konsumenten skaliert
     *      (1000 = neutral, <1000 = Rabatt, >1000 = Aufschlag).
     *
     *      Der Multiplikator gilt bewusst für den Konsumenten (Käufer) und
     *      nicht für den Produzenten - er hängt an dessen eigener
     *      Prognose-Genauigkeit.
     *
     *      getPriceMultiplier() ist `view` und kann keinen State verändern -
     *      anders als bei batteryManager.decideAction() braucht es hier
     *      deshalb kein try/catch.
     */
    function getEffectivePrice(address consumer) public view returns (uint256) {
        if (address(incentiveController) == address(0)) {
            return energyPricePerKwh;
        }
        uint256 multiplier = incentiveController.getPriceMultiplier(consumer);
        return (energyPricePerKwh * multiplier) / 1000;
    }

    /// @notice Helper: Berechnet den Token-Betrag für eine Energiemenge zum Basispreis.
    /// @dev Wh -> kWh: Division durch 1000.
    function calculateCost(uint256 energyWh) public view returns (uint256) {
        return (energyWh * energyPricePerKwh) / 1000;
    }

    /// @notice Wie calculateCost(), aber inklusive Incentive-Multiplikator des Konsumenten.
    /// @dev Wird in _matchAndTrade() pro Match verwendet.
    function calculateCostFor(address consumer, uint256 energyWh) public view returns (uint256) {
        return (energyWh * getEffectivePrice(consumer)) / 1000;
    }
}
