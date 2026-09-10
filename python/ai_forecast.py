"""
ai_forecast.py

STARTER-CODE - Teams ersetzen das Modell durch eigene Wahl.
Nur relevant für Phase 3 (optional).

Lädt historische Daten aus data/history.db (vom Simulator befüllt),
trainiert ein Prognose-Modell und schreibt stündliche Vorhersagen
in den IncentiveController-Contract.

Standardmodell: Lineare Regression auf einfachen Features.
Erwartung: Teams ersetzen mit XGBoost, LSTM, Prophet, etc.

Voraussetzung:
  - Datensammlung: oracle_writer.py muss vorher >24h gelaufen sein,
    damit die Historie aufgebaut wurde
  - .env mit AI_PRIVATE_KEY (autorisiert in IncentiveController)
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from sklearn.ensemble import GradientBoostingRegressor
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"
ABI_DIR = Path(__file__).parent / "abi"
DB_PATH = Path(__file__).parent.parent / "data" / "history.db"

PRIVATE_KEY = os.getenv("AI_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
if not PRIVATE_KEY:
    print("ERROR: AI_PRIVATE_KEY in .env nicht gesetzt")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────
#  Datenextraktion aus Historie
# ─────────────────────────────────────────────────────────────────────

def load_training_data(household_id: str, min_rows: int = 50):
    """Holt Verbrauch + Wetter + Zeitfeatures aus der lokalen DB."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT m.consumption_wh, m.production_wh, m.sim_hour, m.sim_dayofweek,
                   w.irradiance, w.temperature_c_x10, w.cloud_cover
            FROM meter_history m
            JOIN weather_history w ON m.timestamp = w.timestamp
            WHERE m.household_id = ?
            ORDER BY m.timestamp
        """, (household_id,)).fetchall()
    if len(rows) < min_rows:
        return None, None
    arr = np.array(rows)
    # Targets: Verbrauch, Produktion (Spalten 0 und 1)
    y_consumption = arr[:, 0]
    y_production = arr[:, 1]
    # Features: sim_hour, dayofweek, irradiance, temperature, cloud_cover
    X = arr[:, 2:]
    return X, (y_consumption, y_production)


# ─────────────────────────────────────────────────────────────────────
#  Modell  (HIER ERSETZEN TEAMS)
# ─────────────────────────────────────────────────────────────────────

def _expand_features(X):
    """
    Rohe Features -> erweiterte Features.
    Eingabe-Spalten: [sim_hour, sim_dayofweek, irradiance, temperature, cloud_cover]

    sim_hour und sim_dayofweek sind zyklisch (23h liegt naeher an 0h als an 12h);
    lineare/baumbasierte Modelle profitieren von einer sin/cos-Kodierung statt
    des rohen Zaehlers.
    """
    X = np.asarray(X, dtype=float)
    hour = X[:, 0]
    dow = X[:, 1]
    rest = X[:, 2:]
    cyc = np.column_stack([
        np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0),
        np.sin(2 * np.pi * dow / 7.0), np.cos(2 * np.pi * dow / 7.0),
    ])
    return np.column_stack([cyc, rest])


class ForecastModel:
    """
    Gradient-Boosted Trees (sklearn) auf zyklisch kodierten Zeit-Features plus
    Wetter. Robuster als lineare Regression bei nichtlinearem Tagesgang und
    kleinen Datenmengen, ohne Zusatz-Dependency.

    TODO (Teams): Fuer mehr Genauigkeit XGBoost / LightGBM / LSTM einsetzen
    (siehe requirements.txt). API-Vertrag: train(X, y_c, y_p) und
    predict(X) -> (cons, prod) muessen erhalten bleiben.
    """
    _PARAMS = dict(n_estimators=300, max_depth=3, learning_rate=0.05,
                   subsample=0.9, random_state=42)

    def __init__(self):
        self.model_consumption = GradientBoostingRegressor(**self._PARAMS)
        self.model_production = GradientBoostingRegressor(**self._PARAMS)
        self.is_trained = False

    def train(self, X, y_consumption, y_production):
        Xf = _expand_features(X)
        self.model_consumption.fit(Xf, y_consumption)
        self.model_production.fit(Xf, y_production)
        self.is_trained = True

    def predict(self, X):
        if not self.is_trained:
            raise RuntimeError("Modell nicht trainiert")
        Xf = _expand_features(X)
        cons = self.model_consumption.predict(Xf)
        prod = self.model_production.predict(Xf)
        return np.maximum(0, cons), np.maximum(0, prod)


# ─────────────────────────────────────────────────────────────────────
#  On-chain Submitter
# ─────────────────────────────────────────────────────────────────────

def submit_forecast_onchain(w3, contract, account, household_addr, slot, expected_cons, expected_prod):
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    tx = contract.functions.submitForecast(
        Web3.to_checksum_address(household_addr),
        slot,
        int(expected_cons),
        int(expected_prod)
    ).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": 250_000,
        "maxFeePerGas": w3.to_wei("30", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
    })
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)


def submit_actual_onchain(w3, contract, account, household_addr, slot, actual_cons, actual_prod):
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    tx = contract.functions.submitActual(
        Web3.to_checksum_address(household_addr),
        slot,
        int(actual_cons),
        int(actual_prod)
    ).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": 250_000,
        "maxFeePerGas": w3.to_wei("30", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
    })
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)


# ─────────────────────────────────────────────────────────────────────
#  Hauptschleife
# ─────────────────────────────────────────────────────────────────────

def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    bc = config["blockchain"]
    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    account = w3.eth.account.from_key(PRIVATE_KEY)

    ic_addr = bc.get("incentive_controller_address", "")
    submit_enabled = Web3.is_address(ic_addr) and int(ic_addr, 16) != 0
    if not submit_enabled:
        print("ℹ IncentiveController-Adresse nicht gesetzt (config.json) - "
              "Forecasts werden nur berechnet, nicht on-chain eingereicht.\n")

    with open(ABI_DIR / "IncentiveController.json") as f:
        ic_abi = json.load(f)["abi"]
    with open(ABI_DIR / "OracleStorage.json") as f:
        oracle_abi = json.load(f)["abi"]

    incentive = w3.eth.contract(
        address=Web3.to_checksum_address(bc["incentive_controller_address"]),
        abi=ic_abi
    )
    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(bc["oracle_storage_address"]),
        abi=oracle_abi
    )

    print(f"AI Forecaster gestartet, Account: {account.address}\n")

    # Pro Haushalt ein Modell trainieren
    models = {}
    for h in config["households"]:
        X, ys = load_training_data(h["id"])
        if X is None:
            print(f"  ⚠ {h['id']}: Zu wenig Trainingsdaten, skip")
            continue
        m = ForecastModel()
        m.train(X, ys[0], ys[1])
        models[h["id"]] = (m, h)
        print(f"  ✓ {h['id']}: Modell trainiert auf {len(X)} Samples")

    if not models:
        print("Keine Modelle trainiert. Lasse erst oracle_writer.py länger laufen.")
        sys.exit(1)

    print()

    # Forecast-Loop
    while True:
        try:
            current_slot = oracle.functions.getCurrentSlot().call()
            next_slot = current_slot + 1

            # Hole aktuelles Wetter als Feature-Quelle für die Forecast
            weather = oracle.functions.getLatestWeather().call()
            # Features für nächsten Slot:
            sim_hour = (time.localtime().tm_hour + time.localtime().tm_min/60.0) % 24
            sim_dow = time.localtime().tm_wday
            features = np.array([[
                sim_hour, sim_dow,
                weather[0], weather[1], weather[2]
            ]])

            print(f"\n→ Erzeuge Forecast für Slot {next_slot}")

            for h_id, (model, h_cfg) in models.items():
                cons_pred, prod_pred = model.predict(features)
                cons = cons_pred[0]
                prod = prod_pred[0]
                print(f"  {h_id}: Verbrauch={cons:.0f}Wh, Produktion={prod:.0f}Wh")

                if submit_enabled:
                    try:
                        submit_forecast_onchain(
                            w3, incentive, account,
                            h_cfg["address"], next_slot, cons, prod
                        )
                        print(f"    ↳ Forecast on-chain (Slot {next_slot})")
                    except Exception as e:
                        print(f"    ↳ submitForecast fehlgeschlagen: {e}")

            # Nach einem Slot: tatsächliche Werte als "actual" submitten
            time.sleep(60)
            print(f"\n→ Submitte actuals für Slot {current_slot}")
            for h_id, (_, h_cfg) in models.items():
                addr = Web3.to_checksum_address(h_cfg["address"])
                meter = oracle.functions.getLatestMeterReading(addr).call()
                actual_cons = meter[0]
                actual_prod = meter[1]
                print(f"  {h_id}: actual cons={actual_cons}, prod={actual_prod}")

                if submit_enabled:
                    try:
                        submit_actual_onchain(
                            w3, incentive, account,
                            h_cfg["address"], current_slot, actual_cons, actual_prod
                        )
                        print(f"    ↳ Actual on-chain -> Score-Update (Slot {current_slot})")
                    except Exception as e:
                        print(f"    ↳ submitActual fehlgeschlagen: {e}")

        except Exception as e:
            print(f"  Fehler: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
