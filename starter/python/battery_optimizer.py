"""
battery_optimizer.py

STARTER-CODE - Teams implementieren die eigentliche Entscheidungslogik.

Liest aktuellen SoC + Wetterdaten aus dem Oracle und entscheidet,
ob die Batterie geladen, entladen oder im Idle-Zustand bleiben soll.
Die Entscheidung wird via BatteryManager.decideAction() on-chain
protokolliert und der simulierte SoC im Oracle entsprechend angepasst.

Hinweis: Die Entscheidungslogik ist in zwei Varianten verfügbar:
  - In Solidity (BatteryManager.sol → decideAction)
  - In Python (hier)

Teams können WAHLWEISE eine der beiden Varianten implementieren oder beide
kombinieren. Eine Off-Chain-Lösung ist für komplexere Logik einfacher,
On-Chain ist transparenter und prüfbar.
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"
ABI_DIR = Path(__file__).parent / "abi"

PRIVATE_KEY = os.getenv("TRIGGER_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
if not PRIVATE_KEY:
    print("ERROR: TRIGGER_PRIVATE_KEY in .env nicht gesetzt")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────
#  Entscheidungs-Logik (HIER IMPLEMENTIEREN TEAMS)
# ─────────────────────────────────────────────────────────────────────

def decide_action(meter: dict, battery: dict, weather: dict) -> tuple:
    """
    Trifft eine Lade-/Entladeentscheidung.

    Args:
        meter:   {"consumptionWh": int, "productionWh": int, "timestamp": int}
        battery: {"socPercent": int, "capacityWh": int, "maxRateWh": int}
        weather: {"irradianceWm2": int, "temperatureC": int (×10), "cloudCover": int}

    Returns:
        (action: str, amount_wh: int, reason: str)
        action ∈ {"IDLE", "CHARGE", "DISCHARGE"}
    """
    # ─────────────────────────────────────────────────────────────
    # TODO (Teams): Hier eure Strategie implementieren
    #
    # Einfacher Anfang:
    #   surplus = meter["productionWh"] - meter["consumptionWh"]
    #   if surplus > 0 and battery["socPercent"] < 90:
    #       return ("CHARGE", min(surplus, battery["maxRateWh"]),
    #               "PV-Überschuss vorhanden")
    #   elif surplus < 0 and battery["socPercent"] > 20:
    #       return ("DISCHARGE", min(abs(surplus), battery["maxRateWh"]),
    #               "Batterie deckt Defizit")
    #   else:
    #       return ("IDLE", 0, "Keine Aktion nötig")
    #
    # Erweiterte Strategie (Phase 2 Bonus):
    #   - Bei hoher Bewölkung (>80%) Batterie eher entladen, weil
    #     wenig PV erwartet wird
    #   - Bei niedriger Strahlung morgens: schon vorher laden
    #   - Strompreis berücksichtigen, falls dynamisch
    # ─────────────────────────────────────────────────────────────

    return ("IDLE", 0, "Strategie noch nicht implementiert (placeholder)")


# ─────────────────────────────────────────────────────────────────────
#  Hauptschleife (vorgegeben)
# ─────────────────────────────────────────────────────────────────────

def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    bc = config["blockchain"]
    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    account = w3.eth.account.from_key(PRIVATE_KEY)

    with open(ABI_DIR / "OracleStorage.json") as f:
        oracle_abi = json.load(f)["abi"]
    with open(ABI_DIR / "BatteryManager.json") as f:
        bm_abi = json.load(f)["abi"]

    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(bc["oracle_storage_address"]),
        abi=oracle_abi
    )
    battery_mgr = w3.eth.contract(
        address=Web3.to_checksum_address(bc["battery_manager_address"]),
        abi=bm_abi
    )

    households_with_battery = [
        h for h in config["households"] if h["battery_capacity_kwh"] > 0
    ]

    print(f"Battery Optimizer gestartet, Account: {account.address}")
    print(f"Verwalte {len(households_with_battery)} Haushalte mit Batterie\n")

    while True:
        try:
            for h in households_with_battery:
                addr = Web3.to_checksum_address(h["address"])
                meter = oracle.functions.getLatestMeterReading(addr).call()
                battery = oracle.functions.getLatestBatteryState(addr).call()
                weather = oracle.functions.getLatestWeather().call()

                meter_dict = {
                    "consumptionWh": meter[0],
                    "productionWh": meter[1],
                    "timestamp": meter[2]
                }
                battery_dict = {
                    "socPercent": battery[0],
                    "capacityWh": battery[1],
                    "maxRateWh": battery[2]
                }
                weather_dict = {
                    "irradianceWm2": weather[0],
                    "temperatureC": weather[1],
                    "cloudCover": weather[2]
                }

                action, amount, reason = decide_action(meter_dict, battery_dict, weather_dict)
                print(f"  {h['id']}: {action} {amount}Wh - {reason}")

                # ─────────────────────────────────────────────────────
                # TODO (Teams): Entscheidung on-chain protokollieren
                #
                # WICHTIG: Falls ihr P2PEnergyMarket.setBatteryManager() gesetzt
                # habt (siehe README, empfohlene Phase-2-Integration), ruft
                # settleSlot() decideAction() bereits selbst live pro Slot auf -
                # ihr müsst es dann hier NICHT zusätzlich aufrufen (sonst läuft
                # die Entscheidung doppelt: einmal hier, einmal in settleSlot(),
                # mit unnötigen Gaskosten und zwei DecisionMade-Events pro Slot).
                # Dieser Block ist dann nur noch für lokale Sichtbarkeit/Debugging
                # nützlich (siehe print() oben) - keine on-chain-Transaktion nötig.
                #
                # Falls ihr KEINE Integration in P2PEnergyMarket baut und die
                # Entscheidung trotzdem sichtbar/protokolliert haben wollt, ruft
                # sie hier auf (Solidity-Variante macht die ganze Logik):
                #
                # nonce = w3.eth.get_transaction_count(account.address, "pending")
                # tx = battery_mgr.functions.decideAction(addr).build_transaction({...})
                # signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
                # w3.eth.send_raw_transaction(signed.raw_transaction)
                #
                # Falls ihr die Logik in Python lasst, müsst ihr eine zusätzliche
                # Funktion in BatteryManager bauen (z.B. recordDecision)
                # die nur das Resultat speichert.
                # ─────────────────────────────────────────────────────

        except Exception as e:
            print(f"  Fehler: {e}")

        time.sleep(60)


if __name__ == "__main__":
    main()
