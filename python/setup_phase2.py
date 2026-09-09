"""
setup_phase2.py

Einmaliges On-Chain-Setup fuer Phase 2, nach dem Deployment von
BatteryManager und (neu deploytem) P2PEnergyMarket.

  1. Registriert alle Haushalte mit Batteriekapazitaet im BatteryManager
  2. Verknuepft den BatteryManager im Market (setBatteryManager)
  3. Zeigt zur Kontrolle die aktuelle Entscheidung pro Haushalt an

Wichtig: oracle_writer.py waehrend dieses Skripts STOPPEN. Beide senden
von derselben Adresse und kollidieren sonst bei der Nonce.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"
ABI_DIR = Path(__file__).parent / "abi"

ACTION_NAMES = {0: "IDLE", 1: "CHARGE", 2: "DISCHARGE"}


def send(w3, bc, fn, key, account, label):
    """Baut, signiert und sendet eine Transaktion. Gibt True bei Erfolg."""
    try:
        tx = fn.build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": bc["chain_id"],
            "gas": int(fn.estimate_gas({"from": account.address}) * 1.5),
            "maxFeePerGas": w3.to_wei("30", "gwei"),
            "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
        })
        signed = w3.eth.account.sign_transaction(tx, key)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        rec = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        if rec.status == 1:
            print(f"    OK {label}")
            return True
        print(f"    FEHLER {label} (Status 0)")
        return False
    except Exception as e:
        print(f"    FEHLER {label}: {e}")
        return False


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    bc = config["blockchain"]

    for key in ("battery_manager_address", "p2p_market_address"):
        if "REPLACE" in bc.get(key, "REPLACE"):
            print(f"ERROR: {key} in config.json ist nicht gesetzt")
            sys.exit(1)

    deployer_key = os.getenv("DEPLOYER_PRIVATE_KEY")
    if not deployer_key:
        print("ERROR: DEPLOYER_PRIVATE_KEY in .env nicht gesetzt")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    deployer = w3.eth.account.from_key(deployer_key)

    with open(ABI_DIR / "P2PEnergyMarket.json") as f:
        market_abi = json.load(f)["abi"]
    with open(ABI_DIR / "BatteryManager.json") as f:
        battery_abi = json.load(f)["abi"]

    market = w3.eth.contract(
        address=Web3.to_checksum_address(bc["p2p_market_address"]), abi=market_abi)
    battery = w3.eth.contract(
        address=Web3.to_checksum_address(bc["battery_manager_address"]), abi=battery_abi)

    print(f"Market:         {market.address}")
    print(f"BatteryManager: {battery.address}")
    print(f"Deployer:       {deployer.address}")

    # ── 1. Haushalte mit Batterie registrieren ────────────────────
    for h in config["households"]:
        addr = Web3.to_checksum_address(h["address"])
        cap = h.get("battery_capacity_kwh", 0)
        print(f"\n{h['id']}  {addr}  ({cap} kWh)")

        if cap <= 0:
            print("    Keine Batterie laut config.json -> uebersprungen")
            continue

        if battery.functions.isManaged(addr).call():
            print("    Bereits im BatteryManager registriert")
        else:
            send(w3, bc, battery.functions.addHousehold(addr),
                 deployer_key, deployer, "addHousehold()")

    # ── 2. BatteryManager im Market verknuepfen ───────────────────
    print("\nVerknuepfung im Market:")
    current = market.functions.batteryManager().call()
    if current.lower() == battery.address.lower():
        print("    Bereits verknuepft")
    else:
        send(w3, bc, market.functions.setBatteryManager(battery.address),
             deployer_key, deployer, "setBatteryManager()")

    # ── 3. Kontrolle: was wuerde der Manager gerade entscheiden? ──
    # .call() aendert nichts on-chain, zeigt aber die Entscheidung mit den
    # aktuellen Oracle-Daten.
    print("\nAktuelle Entscheidungen (Simulation, kein State-Change):")
    for h in config["households"]:
        addr = Web3.to_checksum_address(h["address"])
        if not battery.functions.isManaged(addr).call():
            continue
        try:
            action, amount = battery.functions.decideAction(addr).call()
            print(f"    {h['id']}: {ACTION_NAMES.get(action, action)} {amount} Wh")
        except Exception as e:
            print(f"    {h['id']}: Aufruf fehlgeschlagen ({e})")

    print("\nFertig. oracle_writer.py und settlement_trigger.py koennen wieder starten.")


if __name__ == "__main__":
    main()