# Energy Trading Challenge – Starter Kit

Dieses Repository enthält Smart Contracts und Python-Skripte für die Challenge.
Lest dieses README vollständig, bevor ihr beginnt.

## Ordnerstruktur

```
contracts/
  ├── interfaces/
  │   ├── IEnergyStablecoin.sol      # Interface des extern bereitgestellten Tokens
  │   ├── IOracleStorage.sol         # Interface zum Lesen aus dem Oracle
  │   ├── IBatteryManager.sol        # Interface für optionale Phase-2-Integration in P2PEnergyMarket
  │   └── IIncentiveController.sol   # Interface für optionale Phase-3-Integration in P2PEnergyMarket
  ├── OracleStorage.sol              # ✅ VOLLSTÄNDIG - nicht ändern, nur deployen
  ├── P2PEnergyMarket.sol            # 🟡 STARTER - Phase 1, settleSlot() implementieren
  ├── BatteryManager.sol             # 🟡 STARTER - Phase 2, decideAction() implementieren
  └── IncentiveController.sol        # 🟡 STARTER - Phase 3 (optional)

python/
  ├── data_simulator.py              # ✅ VOLLSTÄNDIG - simuliert Mess- und Wetterdaten
  ├── oracle_writer.py               # ✅ VOLLSTÄNDIG - schreibt simulierte Daten on-chain
  ├── deploy_helper.py               # ✅ VOLLSTÄNDIG - Hilfsskript zum Deployment
  ├── settlement_trigger.py          # 🟡 STARTER - triggert settleSlot() periodisch
  ├── battery_optimizer.py           # 🟡 STARTER - Lade-/Entladestrategie
  ├── ai_forecast.py                 # 🟡 STARTER - AI-Prognose (Phase 3)
  ├── config.json                    # Konfiguration (Adressen, Haushalte)
  ├── .env.example                   # Vorlage für Private Keys
  └── requirements.txt

data/
  └── history.db                     # SQLite-DB wird vom Simulator angelegt
```

## Setup

### 1. Python-Umgebung

```bash
cd python
python -m venv venv
source venv/bin/activate           # Linux/Mac
# venv\Scripts\activate            # Windows

pip install -r requirements.txt
cp .env.example .env
# .env mit eigenen Private Keys ausfüllen
```

### 2. Solidity-Setup (Hardhat oder Foundry)

Nach Wahl des Teams. Empfehlung: Hardhat.

**Hinweis:** Hardhat entwickelt sich schnell weiter - die genauen Prompts von
`npx hardhat init` (bzw. je nach Version `npx hardhat --init`) ändern sich
zwischen Versionen. Folgt einfach den aktuellen Standard-Vorgaben des
Installers (Node.js 22.13+ wird vorausgesetzt); für dieses Repo braucht ihr
danach nur `npx hardhat compile` zum Laufen zu bringen - Deployment und alle
On-Chain-Interaktionen laufen über die Python-Skripte (`deploy_helper.py`
etc.), nicht über Hardhat selbst. Ob euer Setup ein `hardhat.config.js` oder
`hardhat.config.ts` erzeugt, spielt dafür keine Rolle; ein Sepolia-Netzwerk-
Eintrag in der Hardhat-Config ist für diesen Workflow nicht erforderlich.

```bash
mkdir hackathon && cd hackathon
npx hardhat --init
```

Kopiert die `.sol`-Dateien in `contracts/` und compiliert:

```bash
npx hardhat compile
```

Compilierte Artefakte werden in `artifacts/contracts/<Name>.sol/<Name>.json` abgelegt.
Kopiert sie nach `python/abi/` für die Skripte.

### 3. Deployment-Reihenfolge

1. **OracleStorage** zuerst (keine Args)
   ```bash
   python deploy_helper.py OracleStorage
   ```
   → Adresse in `config.json` unter `oracle_storage_address` eintragen.

2. **P2PEnergyMarket**
   ```bash
   python deploy_helper.py P2PEnergyMarket <stablecoin_addr> <oracle_addr>
   ```

3. **BatteryManager** (Phase 2)
   ```bash
   python deploy_helper.py BatteryManager <oracle_addr>
   ```

4. **IncentiveController** (Phase 3, optional)
   ```bash
   python deploy_helper.py IncentiveController
   ```

5. **Verknüpfung mit P2PEnergyMarket** (Phase 2/3, sobald deployed)
   BatteryManager und IncentiveController sind erst wirksam, wenn ihr sie im
   P2PEnergyMarket eintragt - sonst verhält sich `settleSlot()` weiterhin
   exakt wie in Phase 1, ohne Fehlermeldung (stiller Fallback):
   ```
   p2pMarket.setBatteryManager(batteryManagerAddress)
   p2pMarket.setIncentiveController(incentiveControllerAddress)
   ```

### 4. Oracle autorisieren

Der `oracle_writer.py` braucht ein Wallet, das im OracleStorage als Oracle eingetragen ist.
Standardmässig ist nur der Deployer Oracle. Wenn ihr einen anderen Account nutzen wollt:

```python
# In Hardhat-Konsole oder via ethers.js:
oracleStorage.authorizeOracle("0xORACLE_WALLET_ADDR")
```

### 5. Haushalte registrieren

Pro Team-Mitglied eine Wallet-Adresse generieren und in `config.json` unter `households[].address` eintragen.

Die `oracle_writer.py` ruft beim Start automatisch `registerHousehold()` für alle konfigurierten Adressen auf.

Nach erfolgreicher Registrierung im Oracle: noch im P2PEnergyMarket registrieren:

```python
p2pMarket.registerHousehold("0xHOUSEHOLD_ADDR")
```

### 6. Konsumenten müssen approve() aufrufen

Bevor die Abrechnung läuft, müssen alle Konsumenten dem P2PEnergyMarket Token-Allowance geben:

```python
stablecoin.approve(p2pMarketAddress, BIG_NUMBER)
```

`BIG_NUMBER` = `2**256 - 1` für unbegrenzte Allowance (nur auf Testnet sicher!).

### 7. Skripte starten

In **separaten Terminals**:

```bash
# Terminal 1: Schreibt Mess-Daten on-chain
python oracle_writer.py

# Terminal 2: Triggert die Abrechnung
python settlement_trigger.py

# Terminal 3 (Phase 2): Batterie-Optimierung
python battery_optimizer.py

# Terminal 4 (Phase 3): AI-Prognose
python ai_forecast.py
```

## Was ihr selbst implementieren müsst

Die `TODO`-Markierungen in den Starter-Files zeigen genau, wo eure Arbeit gefordert ist:

**Phase 1 (Pflicht):**
- `P2PEnergyMarket.sol` → `settleSlot()`: Matching-Logik und Token-Transfers
- `settlement_trigger.py`: ggf. Argumente an `settleSlot()` anpassen

**Phase 2 (Pflicht):**
- `BatteryManager.sol` → `decideAction()`: Lade-/Entladestrategie
- `battery_optimizer.py` → `decide_action()`: gleiche Logik in Python (alternativ)
- (Empfohlen) `P2PEnergyMarket.sol` → nach dem Deployment von `BatteryManager`
  einmalig `setBatteryManager(address)` aufrufen und in `settleSlot()` die
  Batterie-Entscheidung in den gehandelten Nettowert einrechnen (siehe
  Kommentare in `settleSlot()` + `interfaces/IBatteryManager.sol`). Ohne
  diesen Schritt läuft der Handel weiter exakt wie in Phase 1, unabhängig
  davon, was `BatteryManager` entscheidet.

**Phase 3 (Optional, Bonus):**
- `IncentiveController.sol` → `_updateScoreForSlot()`, `getPriceMultiplier()`: Incentive-Modell
- `ai_forecast.py` → `ForecastModel`: Modell durch eigenes ersetzen
- Integration in `P2PEnergyMarket.settleSlot()`: nach dem Deployment von
  `IncentiveController` einmalig `setIncentiveController(address)` aufrufen;
  der Preis wird dann pro Match mit dem Multiplikator des Konsumenten
  (Käufer) skaliert (siehe Kommentare in `settleSlot()` +
  `interfaces/IIncentiveController.sol`). Ohne diesen Schritt bleibt der
  Preis exakt wie in Phase 1/2, unabhängig vom Reputationsscore.

## Tipps

- **Logging:** Nutzt Solidity-Events generös. Auf Sepolia Etherscan könnt ihr alle Events nachvollziehen.
- **Debug:** Lokal testen mit Hardhat-Network spart Sepolia-ETH und Zeit.
- **Gas:** Bei Out-of-Gas-Fehlern in `settleSlot()`: auf einzelne Haushalte aufteilen statt grosser Schleife.
  Wird relevanter, sobald ihr BatteryManager/IncentiveController einbindet (siehe unten) -
  das bedeutet zusätzliche externe Calls pro Haushalt in `settleSlot()`.
- **Decimals:** Stablecoin nutzt 6 Decimals. 1 Token = 1_000_000 Token-Units.
- **Neustart:** Simulierte Tageszeit und Batterie-SoC leben nur im Prozessspeicher von
  `oracle_writer.py`. Ein Neustart des Skripts (z.B. beim Debuggen) setzt beides zurück
  auf den Startwert (Sim-Zeit=0, SoC=50%) - das ist erwartetes Verhalten, kein Bug.
- **Slot-Timing:** Ziel ist 1 Slot/Minute, aber der Oracle-Writer sendet dafür bis zu 8
  sequenzielle Transaktionen pro Slot. Bei ~12s Blockzeit auf Sepolia kann das die 60s
  leicht überschreiten - `currentSlot` (läuft nach `block.timestamp`) kann dabei auch mal
  springen. Plant eure Logik so, dass sie nicht auf exakte 60s-Abstände angewiesen ist
  (siehe Hinweis in `oracle_writer.py`).

## Bewertung

Siehe Challenge-Beschreibung. Phase 1+2 = 80 Punkte, Phase 3 = +20 Bonuspunkte.
Code-Qualität (Lesbarkeit, Tests, Events, Sicherheit) zählt.

Viel Erfolg!
