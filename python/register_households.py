"""
register_households.py

Einmal-Skript: registriert alle in config.json ("households") eingetragenen
Adressen on-chain in P2PEnergyMarket und BatteryManager.

Reihenfolge (von den Contracts erzwungen):
  1. OracleStorage.registerHousehold()  -> macht oracle_writer.py automatisch
     (register_households_if_needed() beim Start). Muss ZUERST passiert sein,
     sonst reverten die beiden Aufrufe unten mit "Not in oracle".
  2. P2PEnergyMarket.registerHousehold()  (onlyOwner = Deployer)
  3. BatteryManager.addHousehold()        (onlyOwner = Deployer)

Idempotent: bereits registrierte Haushalte werden übersprungen.

Voraussetzung:
  - .env mit DEPLOYER_PRIVATE_KEY (== owner von P2PEnergyMarket & BatteryManager)
  - config.json mit deployten Contract-Adressen und Haushalts-Adressen
  - oracle_writer.py wurde mindestens einmal erfolgreich gestartet
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

PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")
if not PRIVATE_KEY:
    print("ERROR: DEPLOYER_PRIVATE_KEY in .env nicht gesetzt")
    sys.exit(1)

with open(CONFIG_PATH) as f:
    config = json.load(f)

bc = config["blockchain"]
w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

account = w3.eth.account.from_key(PRIVATE_KEY)


def load_contract(name: str, address: str):
    with open(ABI_DIR / f"{name}.json") as f:
        abi = json.load(f)["abi"]
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def send_tx(fn, retries: int = 3):
    for attempt in range(retries):
        try:
            nonce = w3.eth.get_transaction_count(account.address, "pending")
            tx = fn.build_transaction({
                "from": account.address,
                "nonce": nonce,
                "chainId": bc["chain_id"],
                "gas": 250_000,
                "maxFeePerGas": w3.to_wei("30", "gwei"),
                "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
            })
            signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"    TX: {tx_hash.hex()}")
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            status = "OK" if receipt.status == 1 else "REVERTED"
            print(f"    -> {status} (Block {receipt.blockNumber}, Gas {receipt.gasUsed})")
            return receipt.status == 1
        except Exception as e:
            print(f"    Versuch {attempt + 1} fehlgeschlagen: {e}")
    return False


def main():
    oracle = load_contract("OracleStorage", bc["oracle_storage_address"])
    p2p = load_contract("P2PEnergyMarket", bc["p2p_market_address"])
    battery = load_contract("BatteryManager", bc["battery_manager_address"])

    bal = w3.from_wei(w3.eth.get_balance(account.address), "ether")
    print(f"Deployer / owner : {account.address}")
    print(f"Sepolia ETH      : {bal}")

    for label, c in (("P2PEnergyMarket", p2p), ("BatteryManager", battery)):
        on_chain_owner = c.functions.owner().call()
        mark = "OK" if on_chain_owner.lower() == account.address.lower() else "MISMATCH"
        print(f"{label:16} owner = {on_chain_owner}  [{mark}]")
        if mark == "MISMATCH":
            print("  ABBRUCH: dieses Wallet ist nicht owner - Registrierung wuerde reverten.")
            sys.exit(1)

    for h in config["households"]:
        addr = Web3.to_checksum_address(h["address"])
        print(f"\n--- {h['id']}  {addr} ---")

        if not oracle.functions.isHouseholdRegistered(addr).call():
            print("  Oracle: NICHT registriert -> erst oracle_writer.py starten. Uebersprungen.")
            continue
        print("  Oracle: registriert")

        if p2p.functions.isRegistered(addr).call():
            print("  P2PEnergyMarket: bereits registriert")
        else:
            print("  P2PEnergyMarket: registriere ...")
            send_tx(p2p.functions.registerHousehold(addr))

        if battery.functions.isManaged(addr).call():
            print("  BatteryManager: bereits verwaltet")
        else:
            print("  BatteryManager: addHousehold ...")
            send_tx(battery.functions.addHousehold(addr))

    print("\nFertig.")
    print("P2P  households  :", p2p.functions.getAllHouseholds().call())
    print("Battery managed  :", battery.functions.getManagedHouseholds().call())


if __name__ == "__main__":
    main()
