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
# source venv/bin/activate       # Linux/Mac
venv\Scripts\activate            # Windows

pip install -r requirements.txt
cp .env.example .env
# .env mit eigenen Private Keys ausfüllen
```

### 2. Solidity-Setup 
```bash
mkdir hackathon && cd hackathon
npx hardhat --init
```

Kopiert die `.sol`-Dateien in `contracts/` und compiliert:

```bash
npx hardhat compile
```

Compilierte Artefakte werden in `artifacts/contracts/<Name>.sol/<Name>.json` abgelegt.
Kopere die `*.json` Files nach `python/abi/` für die Skripte.

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
     → Adresse in `config.json` unter `p2p_market_address` eintragen.

3. **BatteryManager** (Phase 2)
   ```bash
   python deploy_helper.py BatteryManager <oracle_addr>
   ```
  → Adresse in `config.json` unter `battery_manager_address` eintragen.

  *Not yet built*
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

### 4 Jupiter Notebook

Das Jupter Notebook registriert Oracle, P2P Contract und setzt das Approval der Haushalte. 

Dafür müssen die Public Keys und Adressen der Contracts im `config.json` und `.env` File gesetzt sein.

#### 4.1 Oracle autorisieren

Der `oracle_writer.py` braucht ein Wallet, das im OracleStorage als Oracle eingetragen ist.
Standardmässig ist nur der Deployer Oracle. Wenn ihr einen anderen Account nutzen wollt:

```python
# In Hardhat-Konsole oder via ethers.js:
oracleStorage.authorizeOracle("0xORACLE_WALLET_ADDR")
```

#### 4.2 Haushalte registrieren

Pro Team-Mitglied eine Wallet-Adresse generieren und in `config.json` unter `households[].address` eintragen.

Die `oracle_writer.py` ruft beim Start automatisch `registerHousehold()` für alle konfigurierten Adressen auf.

Nach erfolgreicher Registrierung im Oracle: noch im P2PEnergyMarket registrieren:

```python
p2pMarket.registerHousehold("0xHOUSEHOLD_ADDR")
```

#### 4.3 Konsumenten müssen approve() aufrufen

Bevor die Abrechnung läuft, müssen alle Konsumenten dem P2PEnergyMarket Token-Allowance geben:

```python
stablecoin.approve(p2pMarketAddress, BIG_NUMBER)
```

`BIG_NUMBER` = `2**256 - 1` für unbegrenzte Allowance (nur auf Testnet sicher!).

### 5. Skripte starten

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

