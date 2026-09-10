# Energy Trading Challenge – Team 3_Horizons

Stand: 09.09.2026, Ende Tag 1. Phase 1 und Phase 2 laufen auf Sepolia.

---

## Deployte Contracts (Sepolia)

| Contract | Adresse | Etherscan |
|---|---|---|
| OracleStorage | `0x61769c1C299495194D7Df49030019e613d13a88D` | verifiziert |
| BatteryManager | `0x8C71b36c6b2a4C61ADAe5394f81be064F3769C01` | verifiziert |
| P2PEnergyMarket | `0xdD29CbA9F18086BB9EC5C7AA4B70F7631B4AA5Fc` | verifiziert |
| Stablecoin (CHFD) | `0x4BaBBaE33998fEd65Ada53bc41CeAa3Cde7F7B46` | vorgegeben |

Diese Adressen stehen auch in `python/config.json` und werden von allen
Skripten von dort gelesen. **Wenn ihr neu deployt, muss config.json angepasst
werden** – sonst reden die Skripte mit dem alten Contract weiter.

Ältere Deployments aus Tag 1 (nicht mehr verwenden):
`0x7E8e46bB`, `0x24887Bc8`, `0xa636ffb9`, `0x7525F4dd` (Market),
`0xEb57FA7D` (Oracle aus dem Pirakash-Branch).

---

## Was ist implementiert

### Phase 1 – P2P-Handel (fertig)

`P2PEnergyMarket.settleSlot()` rechnet einen Slot komplett ab:

1. Slot vom Oracle holen, gegen `lastSettledSlot` prüfen (`>`, nicht `>=`)
2. Pro Haushalt Meter-Daten lesen, netto = Produktion − Verbrauch
3. Netto > 0 → Produzent, netto < 0 → Konsument
4. Die grössere Marktseite proportional herunterskalieren
5. Zwei-Zeiger-Matching über beide Listen
6. Pro Match `transferFrom(consumer, producer, betrag)`

Preis: `energyWh × 100_000 / 1000`, also 0.10 CHFD pro kWh.

### Phase 2 – Batterie und Wetter (fertig)

`BatteryManager.decideAction()` entscheidet pro Haushalt und Slot.
`settleSlot()` ruft die Entscheidung über `_applyBatteryDecision()` ab und
korrigiert das Netto **vor** der Klassifizierung in Produzent/Konsument.

### Phase 3 – Incentives (offen)

`IncentiveController.getPriceMultiplier()` gibt weiterhin konstant 1000
zurück, `_updateScoreForSlot()` ist leer. Der Contract ist nicht deployed.
Die Anbindung in `settleSlot()` fehlt ebenfalls.

---

## Optimierungsstrategie der Batterie

*(Lieferobjekt Phase 2: „Dokumentation der Optimierungsstrategie")*

### Entscheidungsregel

```
Überschuss vorhanden und SoC < 90 %        →  CHARGE
Defizit vorhanden und SoC > Untergrenze    →  DISCHARGE
sonst                                       →  IDLE
```

Damit ist die von der Challenge geforderte Priorisierung
**PV-Eigenverbrauch > Batterie laden > Netz einspeisen** abgebildet: Der
Eigenverbrauch steckt bereits im Netto (Produktion minus Verbrauch), die
Batterie greift auf den Rest zu, und erst was danach übrig bleibt, geht in
den Handel.

### Mengenbegrenzung

Die Lade- bzw. Entlademenge ist dreifach begrenzt:

| Grenze | Warum |
|---|---|
| tatsächlicher Überschuss / tatsächliches Defizit | verhindert, dass ein Produzent durch die Korrektur rechnerisch zum Konsumenten wird |
| `maxRateWh` aus dem Oracle | physikalische Lade-/Entladeleistung pro Slot |
| freier Platz bis 90 % bzw. Energie oberhalb der Untergrenze | Kapazitätsgrenze und Tiefentladeschutz |

Die erste Grenze ist die wichtigste: Der Market zieht `amountWh` vom Netto ab
bzw. addiert es. Ohne diese Begrenzung könnte ein Haushalt mit 200 Wh
Überschuss und 3000 Wh Laderate plötzlich als Konsument mit 2800 Wh Defizit
im Markt auftauchen.

### Wetterabhängige Untergrenze

Normalerweise wird bis 20 % SoC entladen. Meldet das Wetter-Oracle
**über 70 % Bewölkung**, gilt stattdessen 40 %.

Begründung: Viel Bewölkung heisst wenig PV im nächsten Tagesabschnitt. Wer
den Speicher jetzt leerfährt, muss später teuer zukaufen. Die höhere Reserve
kostet kurzfristig etwas Autarkie und sichert dafür die nächste Nacht ab.

Konstanten in `BatteryManager.sol`: `MAX_SOC = 90`, `MIN_SOC = 20`,
`MIN_SOC_BAD_WEATHER = 40`, `CLOUD_THRESHOLD = 70`.

### Warum die Entscheidung live in settleSlot() fällt

`decideAction()` wird innerhalb derselben Transaktion aufgerufen, in der auch
gehandelt wird – nicht vorher über ein separates Skript. So gehört die
Entscheidung garantiert zum selben Slot wie die Meter-Daten. Ein vorher
getriggerter Batterie-Lauf könnte sonst eine veraltete Entscheidung aus dem
Vor-Slot liefern.

### Bekannte Einschränkung

Der simulierte SoC im OracleStorage läuft unabhängig weiter – er folgt im
Simulator nur Produktion und Verbrauch. Der BatteryManager kann ihn nicht
zurückschreiben. Wirksam wird die Entscheidung ausschliesslich über die
Korrektur der gehandelten Energiemenge. Das ist eine Vorgabe des Starter-Kits,
kein Fehler in unserer Implementierung.

---

## Robustheitsfix in settleSlot()

Der ursprüngliche Code rief `transferFrom()` ungeschützt auf. Ein einziger
Haushalt ohne `approve()` oder ohne Guthaben liess damit die **gesamte**
Slot-Abrechnung reverten – auch für alle anderen. Lokal gegen eine
EVM-Testinstanz reproduziert.

Behoben durch:

- `transferFrom()` in try/catch, zusätzlich Prüfung des Rückgabewerts
  (manche Token geben `false` zurück statt zu reverten)
- neues Event `ConsumerSkipped`, damit Setup-Fehler im Trigger-Log sichtbar
  werden statt still zu blockieren
- `lastSettledSlot` wird **vor** den Token-Calls gesetzt
  (Checks-Effects-Interactions), plus `nonReentrant`-Modifier

---

## Setup von null

### 1. Python

```powershell
cd python
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` anlegen (Vorlage: `.env.example`). Benötigte Einträge:

```
DEPLOYER_PRIVATE_KEY=...
ORACLE_PRIVATE_KEY=...        # darf derselbe sein
TRIGGER_PRIVATE_KEY=...       # besser ein eigener, siehe Fallstricke
HOUSE_01_PRIVATE_KEY=...      # für automatisches approve()
HOUSE_02_PRIVATE_KEY=...
HOUSE_03_PRIVATE_KEY=...
```

### 2. Contracts kompilieren

```powershell
cd hackathon
npx hardhat compile
```

Erwartet: `Compiled N Solidity files with solc 0.8.34 (evm target: cancun)`.
Warnungen aus `IncentiveController.sol` sind normal – der Contract ist noch
Starter-Code.

ABIs in den Python-Ordner kopieren:

```powershell
Copy-Item artifacts\contracts\P2PEnergyMarket.sol\P2PEnergyMarket.json ..\python\abi\ -Force
Copy-Item artifacts\contracts\OracleStorage.sol\OracleStorage.json ..\python\abi\ -Force
Copy-Item artifacts\contracts\BatteryManager.sol\BatteryManager.json ..\python\abi\ -Force
```

`python/abi/` steht in der `.gitignore` – nach einem frischen Clone muss der
Ordner also neu befüllt werden.

### 3. Deployment (nur wenn nötig)

Die Contracts oben laufen bereits. Ein Redeploy ist nur nötig, wenn ihr den
Contract-Code ändert. Reihenfolge:

```powershell
python deploy_helper.py OracleStorage
python deploy_helper.py BatteryManager <oracle_addr>
python deploy_helper.py P2PEnergyMarket <stablecoin_addr> <oracle_addr>
```

Adressen danach in `config.json` eintragen.

### 4. On-Chain-Setup nach jedem Redeploy

`registerHousehold()` und `approve()` hängen an der Contract-Adresse und
gelten nach einem Redeploy **nicht mehr**. Zwei Skripte erledigen das:

```powershell
python oracle_writer.py     # kurz laufen lassen, registriert im Oracle
# Strg+C nach 2-3 Slots
python setup_market.py      # Haushalte im Market + Allowances
python setup_phase2.py      # addHousehold() + setBatteryManager()
```

`setup_phase2.py` zeigt am Ende, was der BatteryManager mit den aktuellen
Daten entscheiden würde – praktisch zum Prüfen der Heuristik, ohne auf den
nächsten Slot zu warten.

### 5. Simulation starten

Zwei Terminals, beide mit aktivierter venv:

```powershell
python oracle_writer.py        # Terminal 1
python settlement_trigger.py   # Terminal 2
```

---

## Fallstricke (alle an Tag 1 aufgetreten)

**Nonce-Konflikte.** `DEPLOYER_PRIVATE_KEY` und `ORACLE_PRIVATE_KEY` sind
dieselbe Adresse. Solange `oracle_writer.py` läuft, schlägt jede andere
Transaktion von diesem Konto mit `replacement transaction underpriced` oder
`nonce too low` fehl. **Vor jedem Deploy und jedem Setup-Skript den
Oracle-Writer stoppen.**

**Simulierte Uhrzeit.** Die Sim-Zeit lebt nur im Prozessspeicher von
`oracle_writer.py`. Jeder Neustart setzt sie auf 0 zurück, also Mitternacht –
und nachts wird nichts gehandelt. Eine Sim-Stunde = 240 reale Sekunden, bis
zum Sonnenaufgang bei Sim-h 6 dauert es also 24 Minuten. Den Writer
durchlaufen lassen.

**Handelsvolumen hängt an config.json.** Wenn alle Haushalte PV haben, sind
sie tagsüber alle Produzenten und nachts alle Konsumenten – es gibt schlicht
keine Gegenpartei, `totalEnergyTraded` bleibt 0. Das ist korrektes Verhalten,
kein Bug. Für sichtbaren Handel muss mindestens ein Haushalt
`consumer_only` sein (aktuell house_03).

**Gasverbrauch.** Der Oracle-Writer sendet acht Transaktionen pro Slot, rund
0.0005 Sepolia-ETH. 0.4 ETH reichen für etwa vier Stunden Dauerbetrieb.
Faucet: `https://cloud.google.com/application/web3/faucet/ethereum/sepolia`
(nur Google-Konto nötig, kein Mainnet-Guthaben).

**Trigger revertet regelmässig.** `settlement_trigger.py` sendet blind jede
Minute. Braucht der Oracle für seine acht Transaktionen länger als 60
Sekunden, gibt es keinen neuen Slot und `settleSlot()` revertet – Gas ist
weg. Eine Vorprüfung (`getCurrentSlot()` gegen `lastSettledSlot()`) und ein
Dry-Run per `eth_call` würden das vermeiden.

**Verify braucht Umgebungsvariablen.** Hardhat 3 liest die `.env` nicht
automatisch. Vor `npx hardhat verify` in derselben PowerShell-Sitzung setzen:

```powershell
$env:DEPLOYER_PRIVATE_KEY="..."
$env:ETHERSCAN_API_KEY="..."
```

Verify-Befehle mit Constructor-Argumenten:

```powershell
npx hardhat verify --network sepolia <oracle_addr>
npx hardhat verify --network sepolia <battery_addr> <oracle_addr>
npx hardhat verify --network sepolia <market_addr> <stablecoin_addr> <oracle_addr>
```

Blockscout- und Sourcify-Fehler sind egal, nur Etherscan zählt.

---

## Offen für Tag 2

- **Ladestand-Visualisierung** (Phase-2-Lieferobjekt): SoC-Verlauf über Zeit
  als Plot oder CLI-Output
- **Phase 3**: `getPriceMultiplier()` und `_updateScoreForSlot()`
  implementieren, `IncentiveController` deployen, Preisanbindung in
  `settleSlot()`
- **Trigger-Vorprüfung** portieren, spart Gas
- **`P2P_alt.sol`** aus `hackathon/contracts/` entfernen – wird mitkompiliert
  und kann beim Deployen zu mehrdeutigen Artefaktnamen führen
- **Demo-Ablauf** für die Präsentation festlegen und einmal proben

---

## Sicherheitshinweis

Die Private Keys in `python/.env` und `hackathon/.env` sind während der
Entwicklung mehrfach über Chats und ZIP-Archive gelaufen. Sie sind reine
Testnet-Wallets, aber: **nach dem Hackathon neue Wallets erzeugen** und diese
Keys nirgends wiederverwenden. Der Etherscan-API-Key sollte ebenfalls
gelöscht und neu angelegt werden.

Beide `.env`-Dateien sind über `**/.env` in der `.gitignore` und liegen nicht
im Repository.