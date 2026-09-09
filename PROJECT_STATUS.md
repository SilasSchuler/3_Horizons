# Projekt-Status: Energy Trading Challenge

Zusammenfassung des aktuellen Stands, der wichtigsten Entscheidungen und
offener Punkte. Ergänzt die technischen READMEs ([`README.md`](README.md),
[`python/README.md`](python/README.md)) um den "warum" und "wie sind wir
hierhergekommen"-Teil.

## 1. Was das Projekt macht

Ein P2P-Energiehandel-System: Haushalte mit eigener PV-Erzeugung handeln
Überschüsse direkt untereinander über Smart Contracts ab, statt an einen
zentralen Versorger zu verkaufen. Vier Contracts arbeiten zusammen:

- **OracleStorage** – zentraler On-Chain-Speicher für Mess- und Wetterdaten
- **P2PEnergyMarket** – matched Produzenten mit Konsumenten und rechnet in
  Stablecoin ab
- **BatteryManager** – optionale Lade-/Entladestrategie pro Haushalt
- **IncentiveController** – optionales Reputationssystem, das gute
  Verbrauchsprognosen mit besseren Preisen belohnt

Design-Prinzip: Battery- und Incentive-Integration sind **lose gekoppelt**
über Interfaces und per Setter (nicht Constructor) aktivierbar. Solange
`address(0)` gesetzt ist, verhält sich `settleSlot()` exakt wie in der
Vorphase — stiller Fallback statt Fehler. Das erlaubt eine feste
Deploy-Reihenfolge ohne zirkuläre Abhängigkeiten.

## 2. Was implementiert wurde

### `P2PEnergyMarket.settleSlot()` (Phase 1)

Aufgeteilt in vier interne Helper-Funktionen, die exakt der
Logik-Beschreibung im Contract-Kopf entsprechen:

1. `_readMeterData()` — liest alle Meter-Daten aus dem Oracle
2. `_calculateNetPositions()` — Netto = Produktion − Verbrauch, inkl.
   optionaler BatteryManager-Anpassung (live in derselben TX, damit die
   Entscheidung garantiert zu den gehandelten Meter-Daten passt)
3. `_matchProducersConsumers()` — klassifiziert in Produzenten/Konsumenten
   und matched per **sequenziellem Draining**: zwei Zeiger arbeiten die
   jeweils kleineren Reste ab, bis eine Seite leer ist
4. `_settleTrades()` — transferiert Stablecoin, wendet optional den
   IncentiveController-Preismultiplikator an, emittiert Events

**Bewusste Design-Entscheidung:** sequenzielles Draining statt proportionaler
Verteilung. Erzeugt maximal `Produzenten + Konsumenten` Matches/Events statt
`O(n²)` bei voller proportionaler Aufteilung über alle Paare — spart bei
vielen Haushalten spürbar Gas, bleibt aber vollständig, solange
Gesamtangebot und -nachfrage übereinstimmen.

### `BatteryManager.decideAction()` (Phase 2)

Heuristik mit vier Fällen:
- Überschuss + SoC < 90% → **CHARGE** (begrenzt durch Rate/Kapazität)
- Defizit + SoC über Reserve-Schwelle → **DISCHARGE**
- Reserve-Schwelle ist **wetterabhängig**: normal 20% SoC, aber 40% wenn
  `cloudCover > 80%` — bei absehbar wenig PV-Nachschub wird die Batterie
  stärker geschont
- Sonst **IDLE**

Jede Entscheidung wird mit lesbarem `reason`-String geloggt (z. B.
`"deficit-discharge-cloudy-reserve"`) — auf Etherscan nachvollziehbar.

## 3. Deployment-Chronologie (Sepolia)

| Schritt | Contract | Adresse | Bemerkung |
|---|---|---|---|
| 0 | Stablecoin (CHFD) | `0x4BaBBaE33998fEd65Ada53bc41CeAa3Cde7F7B46` | vom Organisator, verifiziert via `name()/symbol()/decimals()` on-chain |
| 0 | OracleStorage | `0xC9e5540F25aF5435D5AaCfAd848125b3931b4d45` | bereits vorhanden, nicht selbst deployed |
| 1 | P2PEnergyMarket | `0x7F92Ac59007ac159e152FDae4148747A5F106f97` | deployed mit ~2.19M Gas (geschätzt) |
| 2 | BatteryManager | `0x474AB0c40CaE4447504B0C6940E49a4458836ef1` | deployed mit ~1.54M Gas (geschätzt) |
| — | IncentiveController | — | **offen**, Phase 3 optional |

Verknüpft:
- `p2pMarket.setBatteryManager(batteryManager)` ✓
- `oracle.registerHousehold(...)` + `batteryManager.addHousehold(...)` für
  eine Test-Adresse ✓ (Achtung: diese Adresse ist **nicht mehr** in
  `config.json`, siehe Punkt 5)

## 4. Spannende/lehrreiche Punkte aus dem Prozess

- **Toter RPC-Endpoint:** Der in `config.json` voreingetragene
  `https://rpc.sepolia.org` antwortete mit 404. Gewechselt auf
  `https://ethereum-sepolia-rpc.publicnode.com`. Lohnt sich, bei
  Verbindungsproblemen zuerst zu prüfen, bevor man Private Keys oder Contract
  Code verdächtigt.
- **Gas-Limit ≠ Gas-Kosten:** Ein Node reserviert `gasLimit × maxFeePerGas`
  als Guthaben-Check, auch wenn am Ende viel weniger tatsächlich verbraucht
  wird. Der erste Deploy-Versuch schlug mit `insufficient funds` fehl, weil
  `6_000_000 × 30 gwei = 0.18 ETH` mehr war als das Guthaben (0.134 ETH) —
  obwohl der tatsächliche Bedarf nur ~2.19M Gas war. Fix: vorher mit
  `estimate_gas()` schätzen, dann realistisches Limit mit Puffer setzen.
- **Contract-Größe wächst mit Feature-Umfang:** `P2PEnergyMarket` ist durch
  die vier Helper-Funktionen von ~0 Bytes (nur `revert`) auf ~10 KB
  Creation-Bytecode gewachsen — mehr als das Deployment-Gas-Limit der
  ursprünglichen Starter-Vorlage vorsah.
- **On-Chain-Verifikation statt Vertrauen:** Bei den vom User gepasteten
  Adressen wurde nicht blind übernommen, was sie "sein sollen" — stattdessen
  per `eth_call` gegen `decimals()/symbol()/name()` verifiziert, dass die
  Stablecoin-Adresse tatsächlich ein ERC-20-artiger Token ist (`"Swiss
  Stablecoin"`, `CHFD`, 6 Decimals), und per `getCode()` geprüft, dass beide
  Adressen überhaupt deployten Code enthalten.
- **Private Key im Klartext geteilt:** Ein Private Key wurde direkt im Chat
  gepostet. Für einen reinen Testnet-Key mit Spielgeld unkritisch, aber als
  Gewohnheit vermeidenswert — Konversationsverläufe sind kein sicherer
  Aufbewahrungsort für Secrets.
- **`deploy_helper.py` erweitert:** Ursprünglich nur eine Konsolen-Ausgabe
  ("trage die Adresse manuell ein"), jetzt schreibt es per Regex-Ersetzung
  automatisch in `config.json` — ohne die restliche Formatierung/Kommentare
  der Datei zu zerstören (kein vollständiges JSON-Reserialisieren, das hätte
  die Leerzeilen/Struktur verändert).

## 5. Offene Punkte / nächste Schritte

- [ ] **Adress-Chaos auflösen:** `config.json` hatte zwischenzeitlich alle 3
  Haushalte auf derselben Platzhalter-Adresse. Jetzt: 2 Haushalte mit echten
  Adressen (`house_01` = Deployer-Wallet, `house_02` = separate Wallet). Die
  vorher on-chain registrierte Test-Adresse (`0x2C4b...653c`) ist verwaist —
  harmlos, aber die beiden *neuen* Adressen sind noch **nicht** in
  Oracle/P2PEnergyMarket/BatteryManager registriert.
- [ ] `ORACLE_PRIVATE_KEY` und `AI_PRIVATE_KEY` in `.env` sind noch
  Platzhalter aus dem Template — vor echtem Betrieb ersetzen.
- [ ] `oracle_writer.py` noch nie gestartet — ohne laufende Meter-/
  Wetterdaten liefert `decideAction()` nur Nullwerte.
- [ ] Konsumenten müssen `stablecoin.approve(p2pMarket, ...)` aufrufen, sonst
  schlägt `settleSlot()` beim Transfer fehl.
- [ ] `settlement_trigger.py` noch nie gestartet.
- [ ] Phase 3 (optional): `IncentiveController._updateScoreForSlot()` und
  `getPriceMultiplier()` sind noch Starter-Code (nur auskommentierte
  TODO-Vorschläge), dann deployen und `setIncentiveController()` aufrufen.
- [ ] `battery_optimizer.py` / `ai_forecast.py` (Python-seitige Alternativen)
  sind ebenfalls noch nicht implementiert.
