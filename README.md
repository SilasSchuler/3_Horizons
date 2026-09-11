# Energy Trading Challenge — Team 3_Horizons

Eine Energiegemeinschaft, in der Haushalte ihren Solarüberschuss direkt
untereinander handeln. Ein Speicher glättet den Tagesverlauf, Agenten
verhandeln über verschiebbare Lasten, und wer seinen Verbrauch zuverlässig
vorhersagt, zahlt weniger.

Alles läuft auf dem Sepolia-Testnet. Jede Zahl in dieser Dokumentation ist
on-chain nachprüfbar.

---

## Contracts

| Contract | Adresse | Aufgabe |
|---|---|---|
| OracleStorage | `0x61769c1C299495194D7Df49030019e613d13a88D` | Zähler- und Wetterdaten |
| P2PEnergyMarket | `0x1c33292F3Fe9720C78606A1bB714E7D8FEd37E02` | Matching und Abrechnung |
| BatteryManager | `0x2d074bC0c484b5012ceA2D2d1378297BF18802eb` | Speicherstrategie |
| IncentiveController | `0x8A4F38Da46371C706DAC7a77AD128542AE2Dc638` | Reputation und Preisfaktor |

Alle auf Etherscan verifiziert. Der Stablecoin (CHFD) unter
`0x4BaBBaE33998fEd65Ada53bc41CeAa3Cde7F7B46` war vorgegeben.

Dieselben Adressen stehen in `python/config.json`. Alle Skripte lesen sie
von dort — **nach einem Redeploy muss die Datei angepasst werden**, sonst
reden die Skripte mit dem alten Contract weiter.

---

## Ordnerstruktur

```
hackathon/
  contracts/                  Solidity-Quellen
    interfaces/               Schnittstellen zu Token, Oracle, Battery, Incentive
    OracleStorage.sol         vorgegeben, unverändert
    P2PEnergyMarket.sol       Phase 1 bis 3
    BatteryManager.sol        Phase 2
    IncentiveController.sol   Phase 3
  hardhat.config.ts

frontend/
  server.js                   Node-Backend, startet die Python-Skripte
  index.html                  Admin-Oberfläche
  config.json                 Adressen für die Oberfläche

python/
  oracle_writer.py            schreibt Messdaten on-chain
  settlement_trigger.py       ruft settleSlot() periodisch auf
  deploy_helper.py            Deployment
  setup_market.py             Haushalte registrieren, Allowances setzen
  setup_phase2.py             BatteryManager verknüpfen
  setup_phase3.py             IncentiveController verknüpfen
  visualize_battery.py        Ladestandsverlauf als Plot
  config.json                 Contract-Adressen und Haushalte
  Phase 3/                    Agentenschicht, eigene README
  helper/                     die vom Frontend aufgerufenen Varianten
  abi/                        entsteht beim Kompilieren, nicht versioniert
```

**Zur Doppelung in `helper/`:** Die Skripte dort sind die Varianten, die das
Frontend über `server.js` aufruft. Sie haben eigene Ein- und Ausgaben, damit
die Weboberfläche die Antworten anzeigen kann. Die Skripte direkt in
`python/` sind für den Betrieb von der Kommandozeile gedacht und werden in
dieser README verwendet. Beide lesen dieselbe `python/config.json`.

Wer von Hand arbeitet, nimmt die Skripte aus `python/`. Wer die Oberfläche
benutzt, muss nichts entscheiden.

**Erzeugte Ordner**, die nach einem frischen Clone fehlen und neu entstehen:
`python/abi/` beim Kopieren der Artefakte, `python/venv/` beim Setup,
`python/logs/` und `python/plots/` beim Betrieb,
`python/Phase 3/protokolle/` bei jedem Verhandlungslauf,
`hackathon/artifacts/` und `node_modules/` beim Kompilieren.

---

## Phase 1 — Direkter Handel

`settleSlot()` rechnet einen Slot vollständig ab:

1. Slot vom Oracle holen und gegen `lastSettledSlot` prüfen
2. Pro Haushalt netto = Produktion minus Verbrauch
3. Netto positiv heisst Produzent, negativ heisst Konsument
4. Die grössere Marktseite proportional herunterskalieren
5. Zwei-Zeiger-Matching über beide Listen
6. Pro Match `transferFrom(consumer, producer, betrag)`

Preis: 0.10 CHFD pro kWh, also `energyWh × 100_000 / 1000`.

### Der Robustheitsfix

Der ursprüngliche Entwurf rief `transferFrom()` ungeschützt auf. Ein
einziger Haushalt ohne Guthaben oder ohne `approve()` liess damit die
**gesamte** Abrechnung reverten — auch für alle anderen. Lokal gegen eine
EVM-Testinstanz reproduziert.

Behoben durch drei Massnahmen: `transferFrom()` läuft in try/catch und der
Rückgabewert wird geprüft, weil manche Token `false` liefern statt zu
reverten. Ein `ConsumerSkipped`-Event macht Setup-Fehler im Log sichtbar,
statt sie still zu verschlucken. Und `lastSettledSlot` wird vor den
Token-Aufrufen gesetzt — Checks-Effects-Interactions, zusammen mit einem
Reentrancy-Schutz.

---

## Phase 2 — Speicher

`BatteryManager.decideAction()` entscheidet pro Haushalt und Slot. Der
Market ruft die Entscheidung über `_applyBatteryDecision()` ab und korrigiert
das Netto **vor** der Klassifizierung in Produzent und Konsument.

### Entscheidungsregel

```
Überschuss vorhanden und SoC < MAX_SOC     →  CHARGE
Defizit vorhanden und SoC > Untergrenze    →  DISCHARGE
sonst                                       →  IDLE
```

Damit ist die geforderte Priorisierung abgebildet — Eigenverbrauch vor
Batterie vor Netz. Der Eigenverbrauch steckt bereits im Netto, die Batterie
greift auf den Rest zu, und erst was danach übrig bleibt, geht in den Handel.

### Mengenbegrenzung

Die Lade- und Entlademenge ist dreifach begrenzt:

| Grenze | Warum |
|---|---|
| tatsächlicher Überschuss oder Defizit | verhindert, dass ein Produzent durch die Korrektur rechnerisch zum Konsumenten wird |
| `maxRateWh` aus dem Oracle | physikalische Leistung pro Slot |
| freier Platz bis MAX_SOC, Energie über der Untergrenze | Kapazität und Tiefentladeschutz |

Die erste ist die wichtigste. Ohne sie könnte ein Haushalt mit 200 Wh
Überschuss und 3000 Wh Laderate plötzlich als Konsument mit 2800 Wh Defizit
im Markt auftauchen.

### Wetterabhängige Untergrenze

Normalerweise wird bis 20 Prozent entladen. Meldet das Wetter-Oracle über 70
Prozent Bewölkung, gilt stattdessen 40. Viel Bewölkung heisst wenig PV im
nächsten Tagesabschnitt — wer den Speicher jetzt leerfährt, muss später teuer
zukaufen.

### Die wichtigste Erkenntnis

Mit einer Ladeobergrenze von 90 Prozent **kam der Handel zum Erliegen**.
Jeder Haushalt füllte zuerst den eigenen Speicher, und für die Nachbarn blieb
nichts. Bei 50 Prozent lädt der Speicher bis Mittag, und was danach kommt,
geht an den Markt.

Gemessen: bei 90 Prozent null Trades, bei 50 Prozent über 13'500 Wh in 178
Slots.

Ein Markt braucht Vielfalt. Wenn alle dieselbe optimale Strategie fahren,
verschwindet der Handel — nicht weil die Technik versagt, sondern weil es
nichts mehr zu tauschen gibt.

### Warum die Entscheidung live fällt

`decideAction()` wird innerhalb derselben Transaktion aufgerufen, in der auch
gehandelt wird, nicht vorher über ein separates Skript. So gehört die
Entscheidung garantiert zum selben Slot wie die Messdaten.

### Bekannte Einschränkung

Der simulierte Ladestand im OracleStorage läuft unabhängig weiter — er folgt
im Simulator nur Produktion und Verbrauch, der BatteryManager kann ihn nicht
zurückschreiben. Wirksam wird die Entscheidung ausschliesslich über die
Korrektur der gehandelten Energiemenge. Das ist eine Vorgabe des
Starter-Kits, kein Fehler der Implementierung.

---

## Phase 3 — Anreiz und Agenten

Aus Phase 2 folgte die Frage, wofür man belohnen soll. Messbar ist
**Prognosegenauigkeit**: Wer vorher sagt, was er verbraucht, macht das Netz
planbar. Wer nicht, zwingt die Gemeinschaft zu Reserve — und die kostet. Im
realen Strommarkt ist das dasselbe Prinzip: Ausgleichsenergie ist teurer als
geplante Energie.

### Der Contract

`IncentiveController` nimmt je Slot zwei Einträge entgegen — die Prognose
vorher, den Ist-Wert nachher. Aus der Abweichung entsteht ein Slot-Score
zwischen 0 und 1000: bis 10 Prozent Abweichung die vollen Punkte, ab 50
Prozent null, dazwischen linear.

Die Schwelle ist bewusst gewählt. Das Rauschen im Simulator liegt bei plus
minus 15 Prozent, die mittlere Abweichung also bei etwa 7.5. Wer seine
Flexibilität steuert, bleibt darunter. Wer rät, nicht.

Der Gesamtscore ist ein gleitender Durchschnitt über acht Slots. Ein
einzelner Ausreisser zerstört die Reputation nicht, dauerhafte Ungenauigkeit
schon. Und es braucht keine Historie im Storage, nur den letzten Wert.

Daraus wird ein Preisfaktor in Promille: 1000 ist neutral, 800 sind 20
Prozent Rabatt, 1200 sind 20 Prozent Aufschlag. Neueinsteiger starten bei
500 Punkten und damit neutral.

Der Market ruft `calculateCostFor(consumer, energyWh)` auf. Auch hier
try/catch, plus eine Plausibilitätsprüfung: Faktoren ausserhalb 500 bis 2000
werden verworfen, damit ein fehlerhafter Controller niemanden ruinieren kann.

### Die Agentenschicht

Um genau zu prognostizieren, muss ein Haushalt seine Geräte selbst einplanen.
Daraus wurde ein Agent je Haushalt, ausserhalb der Chain.

**Zeitfenster** kommen aus echten Kalenderdateien im ICS-Format. Wer bis 17
Uhr im Büro ist, kann mittags keine Waschmaschine starten — das Fenster
verschiebt sich automatisch nach hinten.

**Verhandlung:** Wer Überschuss hat, bietet ihn 20 Prozent unter Marktpreis
an. Der Zuschlag geht an den, der den grössten Anteil seines Bedarfs damit
deckt. Bleibt etwas übrig, geht es an den nächsten — sonst fliesst es zum
schlechteren Tarif ins Netz.

**Teilmengen:** Ein Angebot muss den Bedarf nicht ganz decken. Deckt der
Nachbar 800 von 1200 Wh, kommen die restlichen 400 aus dem Netz, und der
Mischpreis liegt trotzdem darunter.

**Fristen:** Das E-Auto muss um 6 Uhr abfahrbereit sein. Rückt die Frist
näher, nimmt der Agent auch ein Angebot ohne Preisvorteil. Bleibt weniger
Zeit als die Ladedauer, lädt er zum vollen Netzpreis — er spart dann nichts,
hält aber seine Prognose ein.

**Das Sprachmodell** formuliert die Entscheidungen, lokal über Ollama. Es
entscheidet mit, aber es setzt nichts durch: Jede Zusage läuft durch eine
Prüfschicht, die gegen Kalender, Energiebedarf und Doppelbuchung testet.

In einem dokumentierten Lauf hat das Modell dreimal etwas Unmögliches
vorgeschlagen — eine Waschmaschine um 2 Uhr nachts und zweimal eine
unpassende Menge. Alle drei wurden abgefangen, nachzulesen in
`python/Phase 3/protokolle/`.

Details in `python/Phase 3/README.md`.

---

## Ergebnis

| | |
|---|---|
| Reputation mit Agent | 877 und 740 |
| Reputation ohne Agent | 378 |
| Preisunterschied | 15 Prozent Rabatt gegen 4.8 Prozent Aufschlag |
| Prognoseabweichung mit Agent | meist 0 Prozent |
| Prognoseabweichung ohne Agent | 65 bis 153 Prozent |
| Tests | 43 |

house_01 und house_02 sind Prosumer mit PV und Speicher. house_03 ist reiner
Konsument ohne Anlage und ohne verschiebbare Lasten — die Kontrollgruppe,
die die Challenge verlangt.

---

## Setup von null

### 1. Python

```powershell
cd python
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` nach dem Muster von `.env.example` anlegen:

```
DEPLOYER_PRIVATE_KEY=...
ORACLE_PRIVATE_KEY=...        # darf derselbe sein
TRIGGER_PRIVATE_KEY=...       # besser ein eigener
AI_PRIVATE_KEY=...            # muss ein anderer sein als der Oracle
HOUSE_01_PRIVATE_KEY=...
HOUSE_02_PRIVATE_KEY=...
HOUSE_03_PRIVATE_KEY=...
```

### 2. Contracts kompilieren

```powershell
cd hackathon
npx hardhat compile
```

Erwartet: `Compiled N Solidity files with solc 0.8.34 (evm target: cancun)`.

ABIs in den Python-Ordner kopieren — `python/abi/` steht in der
`.gitignore`, nach einem frischen Clone muss der Ordner neu befüllt werden:

```powershell
Copy-Item artifacts\contracts\OracleStorage.sol\OracleStorage.json ..\python\abi\ -Force
Copy-Item artifacts\contracts\P2PEnergyMarket.sol\P2PEnergyMarket.json ..\python\abi\ -Force
Copy-Item artifacts\contracts\BatteryManager.sol\BatteryManager.json ..\python\abi\ -Force
Copy-Item artifacts\contracts\IncentiveController.sol\IncentiveController.json ..\python\abi\ -Force
```

### 3. Deployment

Die Contracts oben laufen bereits. Ein Redeploy ist nur nötig, wenn sich der
Code ändert. **Die Reihenfolge ist zwingend:**

```powershell
python deploy_helper.py OracleStorage
python deploy_helper.py P2PEnergyMarket <stablecoin_addr> <oracle_addr>
python deploy_helper.py BatteryManager <oracle_addr>
python deploy_helper.py IncentiveController
```

Adressen in `config.json` eintragen. BatteryManager und IncentiveController
sind erst wirksam, wenn sie im Market eingetragen sind — sonst verhält sich
`settleSlot()` weiterhin wie in Phase 1, ohne Fehlermeldung.

### 4. On-Chain-Setup nach jedem Redeploy

`registerHousehold()` und `approve()` hängen an der Contract-Adresse und
gelten nach einem Redeploy nicht mehr:

```powershell
python oracle_writer.py     # kurz laufen lassen, registriert im Oracle
# Strg+C nach zwei bis drei Slots
python setup_market.py      # Haushalte im Market plus Allowances
python setup_phase2.py      # addHousehold() und setBatteryManager()
python setup_phase3.py      # authorizeAI() und setIncentiveController()
```

`setup_phase2.py` zeigt am Ende, was der BatteryManager mit den aktuellen
Daten entscheiden würde — praktisch zum Prüfen, ohne auf den nächsten Slot
zu warten.

### 5. Simulation starten

Zwei Terminals, beide mit aktivierter venv:

```powershell
python oracle_writer.py        # Terminal 1, schreibt Messdaten
python settlement_trigger.py   # Terminal 2, rechnet Slots ab
```

### 6. Agenten

Drittes Terminal:

```powershell
cd "Phase 3"
python run_agents.py 12        # Prognosen, Reputation, Verhandlung
python build_dashboard.py      # erzeugt dashboard.html
```

Ohne Blockchain, für die Vorführung:

```powershell
python run_agents.py --nacht 6        # E-Auto mit Frist
python run_agents.py --demo 6         # Tagesverlauf
python run_agents.py --nacht --llm 6  # mit lokalem Sprachmodell
```

### 7. Weboberfläche

```bash
cd frontend
npm install express dotenv tree-kill
node server.js
```

Die Seite ruft die Skripte aus `python/helper/` auf und zeigt deren Ausgabe
direkt an. Das ist eine Admin-Oberfläche, deshalb werden auch Fehler im
Klartext ausgegeben — beim Aufsetzen muss man schnell sehen, was schiefging.

---

## Verifizierung auf Etherscan

Hardhat liest die `.env` nicht automatisch. In derselben Sitzung setzen:

```powershell
$env:DEPLOYER_PRIVATE_KEY="..."
$env:ETHERSCAN_API_KEY="..."

npx hardhat verify --network sepolia <oracle_addr>
npx hardhat verify --network sepolia <market_addr> <stablecoin_addr> <oracle_addr>
npx hardhat verify --network sepolia <battery_addr> <oracle_addr>
npx hardhat verify --network sepolia <incentive_addr>
```

Blockscout- und Sourcify-Fehler sind unerheblich, nur Etherscan zählt.

---

## Fallstricke

**Nonce-Konflikte.** Deployer und Oracle sind dieselbe Adresse. Solange
`oracle_writer.py` läuft, schlägt jede andere Transaktion von diesem Konto
fehl. Vor jedem Deploy und jedem Setup-Skript den Oracle stoppen.

**Der AI-Submitter braucht ein eigenes Konto.** Zeigt `AI_PRIVATE_KEY` auf
dasselbe Konto wie der Oracle, kollidieren die Transaktionen und etwa die
Hälfte der Einreichungen schlägt fehl.

**Simulierte Uhrzeit.** Die Sim-Zeit lebt nur im Prozessspeicher von
`oracle_writer.py`. Jeder Neustart setzt sie auf Mitternacht zurück — und
nachts wird nichts gehandelt. Eine Sim-Stunde sind 240 reale Sekunden, bis
zum Sonnenaufgang bei Sim-h 6 dauert es also 24 Minuten.

**Handelsvolumen hängt an config.json.** Wenn alle Haushalte PV haben, sind
sie tagsüber alle Produzenten und nachts alle Konsumenten — es gibt keine
Gegenpartei. Für sichtbaren Handel muss mindestens ein Haushalt
`consumer_only` sein, aktuell house_03.

**Gasverbrauch.** Der Oracle sendet acht Transaktionen pro Slot, rund 0.0005
Sepolia-ETH. 0.4 ETH reichen für etwa vier Stunden Dauerbetrieb. Faucet:
`https://cloud.google.com/application/web3/faucet/ethereum/sepolia`

**Trigger revertet gelegentlich.** Er sendet blind jede Minute. Braucht der
Oracle länger als 60 Sekunden, gibt es keinen neuen Slot und `settleSlot()`
revertet. Eine Vorprüfung per `eth_call` würde das vermeiden.

**`in-flight transaction limit`** beim Trigger ist ein bekannter Effekt
delegierter Konten auf Sepolia. Er behebt sich nach ein bis zwei Slots von
selbst.

---

## Was wir gelernt haben

**Ein Markt braucht Unterschiede.** Die optimale Einzelstrategie hat den
Handel abgewürgt. Nicht mehr Technik war die Lösung, sondern eine
Begrenzung, die Raum für andere lässt.

**Ein Sprachmodell darf verhandeln, aber nicht entscheiden.** Dass es Fehler
macht, ist dokumentiert — und dass sie abgefangen werden, ebenfalls.

**Ein Ausfall darf nicht alle treffen.** Jede Contract-Anbindung läuft in
try/catch. Fällt der BatteryManager aus, handelt der Market mit
unkorrigierten Werten weiter. Fällt der IncentiveController aus, gilt der
Basispreis. Kann ein Konsument nicht zahlen, wird nur er übersprungen.

---

## Sicherheitshinweis

Die Private Keys in `python/.env` sind während der Entwicklung mehrfach über
Chats und Archive gelaufen. Es sind reine Testnet-Wallets, aber nach dem
Hackathon sollten neue erzeugt und diese nirgends wiederverwendet werden. Der
Etherscan-API-Key ebenso.

Die `.env`-Dateien liegen über `**/.env` in der `.gitignore` und sind nicht
im Repository.