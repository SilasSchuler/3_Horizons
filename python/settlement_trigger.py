"""
settlement_trigger.py

STARTER-CODE - Teams passen die TODO-Blöcke an ihren eigenen Contract an.

Ruft jede Simulationsminute settleSlot() auf dem P2PEnergyMarket-Contract auf.
Damit wird der vom Oracle gefütterte Slot abgerechnet:
  - Produzenten erhalten Stablecoins
  - Konsumenten zahlen Stablecoins

Voraussetzung:
  - .env mit TRIGGER_PRIVATE_KEY (kann derselbe Key wie DEPLOYER sein)
  - config.json mit p2p_market_address gesetzt
  - Konsumenten müssen vorher approve() auf den Stablecoin aufgerufen haben
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


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    bc = config["blockchain"]
    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    account = w3.eth.account.from_key(PRIVATE_KEY)

    with open(ABI_DIR / "P2PEnergyMarket.json") as f:
        market_abi = json.load(f)["abi"]

    market = w3.eth.contract(
        address=Web3.to_checksum_address(bc["p2p_market_address"]),
        abi=market_abi
    )

    print(f"Settlement-Trigger gestartet, Account: {account.address}")
    print(f"Market-Contract: {bc['p2p_market_address']}\n")

    while True:
        try:
            print(f"\n→ Trigger settleSlot() um {time.strftime('%H:%M:%S')}")

            # ─────────────────────────────────────────────────────
            # TODO (Teams): Anpassen falls eure settleSlot() Parameter braucht
            #
            # Beispiele für Erweiterungen:
            #   - settleSlot(uint256 slot)              -> Slot-Argument übergeben
            #   - settleHousehold(address household)    -> einzeln pro Haushalt
            #   - settleSlotWithLimit(uint256 maxGas)   -> mit Gas-Limit
            #
            # Aktuell: nimmt an, dass settleSlot() ohne Argumente aufrufbar ist.
            # ─────────────────────────────────────────────────────

            nonce = w3.eth.get_transaction_count(account.address, "pending")
            tx = market.functions.settleSlot().build_transaction({
                "from": account.address,
                "nonce": nonce,
                "chainId": bc["chain_id"],
                "gas": 1_500_000,                 # eventuell anpassen
                "maxFeePerGas": w3.to_wei("30", "gwei"),
                "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
            })
            signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"  TX: {tx_hash.hex()}")
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                print(f"  ✓ Settlement erfolgreich (Block {receipt.blockNumber})")
            else:
                print(f"  ✗ Settlement fehlgeschlagen!")

        except Exception as e:
            print(f"  Fehler: {e}")

        time.sleep(60)  # 1 Slot warten


if __name__ == "__main__":
    main()
