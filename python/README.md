# Python-Skripte – Energy Trading Challenge

Dieses Verzeichnis enthält alle Python-Skripte für Simulation, Oracle-Betrieb,
Deployment und Settlement der Energy Trading Challenge. Für die Solidity-Seite
siehe [`../hackathon/contracts/`](../hackathon/contracts/).

## Setup

```bash
cd python
python -m venv venv
source venv/bin/activate           # Linux/Mac
# venv\Scripts\activate            # Windows

pip install -r requirements.txt
cp .env.example .env
```

`.env` ausfüllen:

| Variable | Zweck | Wer braucht es |
|---|---|---|
| `DEPLOYER_PRIVATE_KEY` | Contracts deployen | `deploy_helper.py` |
| `ORACLE_PRIVATE_KEY` | Mess-/Wetterdaten on-chain schreiben | `oracle_writer.py` |
| `TRIGGER_PRIVATE_KEY` | `settleSlot()` triggern (kann = Deployer sein) | `settlement_trigger.py`, `battery_optimizer.py` |
| `AI_PRIVATE_KEY` | KI-Prognosen einreichen (Phase 3) | `ai_forecast.py` |

Jede Wallet braucht Sepolia-Test-ETH (z. B. über einen Sepolia-Faucet), um Gas
zu bezahlen. `.env` ist über `.gitignore` von Git ausgeschlossen — **niemals
committen**.

## Konfiguration (`config.json`)

Enthält Simulationsparameter, Haushalte (Adressen, PV-/Batteriegröße) und den
`blockchain`-Block mit RPC-Endpoint, Chain-ID und allen Contract-Adressen.
`deploy_helper.py` trägt neu deployte Adressen dort automatisch ein.

## Deployte Contracts (Sepolia)

| Contract | Adresse | Status |
|---|---|---|
| Swiss Stablecoin (CHFD) | [`0x4BaBBaE33998fEd65Ada53bc41CeAa3Cde7F7B46`](https://sepolia.etherscan.io/address/0x4BaBBaE33998fEd65Ada53bc41CeAa3Cde7F7B46) | vom Organisator bereitgestellt |
| OracleStorage | [`0xC9e5540F25aF5435D5AaCfAd848125b3931b4d45`](https://sepolia.etherscan.io/address/0xC9e5540F25aF5435D5AaCfAd848125b3931b4d45) | deployed |
| P2PEnergyMarket | [`0x7F92Ac59007ac159e152FDae4148747A5F106f97`](https://sepolia.etherscan.io/address/0x7F92Ac59007ac159e152FDae4148747A5F106f97) | deployed |
| BatteryManager | — | offen (Phase 2) |
| IncentiveController | — | offen (Phase 3, optional) |

RPC-Endpoint: `https://ethereum-sepolia-rpc.publicnode.com` (Chain-ID `11155111`).

> Aktueller Stand jederzeit verbindlich in `config.json` unter `blockchain` –
> diese Tabelle ist eine Momentaufnahme und wird bei neuen Deployments nicht
> automatisch mitgepflegt.

## Skript-Übersicht

| Skript | Status | Zweck |
|---|---|---|
| `data_simulator.py` | ✅ fertig | Generiert synthetische Meter-/Wetterdaten (Tagesgang, Batterie-SoC) |
| `oracle_writer.py` | ✅ fertig | Schreibt Simulator-Daten jeden Slot in `OracleStorage` |
| `deploy_helper.py` | ✅ fertig | Deployed Contracts, trägt Adresse automatisch in `config.json` ein |
| `settlement_trigger.py` | 🟡 Starter | Ruft periodisch `settleSlot()` auf `P2PEnergyMarket` auf |
| `battery_optimizer.py` | 🟡 Starter | Off-Chain-Variante der Lade-/Entladeentscheidung (Alternative zu `BatteryManager.sol`) |
| `ai_forecast.py` | 🟡 Starter, optional | Trainiert Prognosemodell aus `data/history.db`, meldet an `IncentiveController` |

Skripte mit "✅ fertig" sind laut Vorgabe **nicht** zu verändern.

### `oracle_writer.py` im Detail

Simuliert einen Messstellenbetreiber: liest pro Slot (Standard: 1 Slot = 60s
real = 15 simulierte Minuten) aktuelle Werte aus `EnergySimulator`
(`data_simulator.py`) und schreibt sie on-chain.

Ablauf pro Slot (`push_slot()`):
1. `updateSlot()` — Slot-Counter im Oracle weiterschalten
2. `updateWeather(irradiance, temperature, cloudCover)` — globale Wetterdaten
3. Pro Haushalt: `updateMeter(household, consumptionWh, productionWh)`, bei
   vorhandener Batterie zusätzlich `updateBattery(household, soc, capacity, maxRate)`

Vor dem ersten Slot ruft `register_households_if_needed()` einmalig
`registerHousehold()` für alle in `config.json` konfigurierten Adressen auf,
die im Oracle noch nicht registriert sind.

Jede Transaktion läuft über `_send_tx()` mit Nonce-Management und bis zu 3
Retries bei Fehlern (z. B. Gas-Preis-Spitzen).

**Bekannte Einschränkungen (kein Bug):**
- Bis zu 8 sequenzielle TXs pro Slot (Slot + Wetter + 2× pro Haushalt) können
  bei ~12s Sepolia-Blockzeit die 60s-Zielslotdauer überschreiten;
  `currentSlot` folgt `block.timestamp` und kann dadurch auch mal springen —
  Contract-Logik (v. a. `settleSlot()`) sollte sich nicht auf exakte
  60s-Abstände verlassen.
- Simulierte Tageszeit und Batterie-SoC leben nur im Prozessspeicher des
  Skripts — ein Neustart setzt beides zurück (Sim-Zeit = 0, SoC = 50%).

Voraussetzung: `ORACLE_PRIVATE_KEY` in `.env`, diese Wallet muss im
`OracleStorage` per `authorizeOracle()` freigeschaltet sein (Standard: nur der
Deployer des Oracle ist initial autorisiert).

Start:
```bash
python oracle_writer.py
```

## Empfohlener Ablauf (mehrere Terminals)

```bash
# Terminal 1: Mess-/Wetterdaten on-chain schreiben
python oracle_writer.py

# Terminal 2: Abrechnung triggern
python settlement_trigger.py

# Terminal 3 (Phase 2): Batterie-Optimierung, falls Python-Variante genutzt wird
python battery_optimizer.py

# Terminal 4 (Phase 3, optional): KI-Prognose
python ai_forecast.py
```

Vor dem ersten Settlement müssen alle Konsumenten dem `P2PEnergyMarket`
Token-Allowance geben:

```python
stablecoin.approve(p2p_market_address, 2**256 - 1)  # nur auf Testnet unbegrenzt sicher
```
