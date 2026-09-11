"""
deploy_helper.py

VOLLSTÄNDIG VORGEGEBEN - Teams modifizieren dieses Skript NICHT.

Hilfs-Skript für Smart-Contract-Deployment auf Sepolia.

Nutzung:
  python deploy_helper.py OracleStorage
  python deploy_helper.py P2PEnergyMarket <stablecoin_addr> <oracle_addr>
  python deploy_helper.py BatteryManager <oracle_addr>
  python deploy_helper.py IncentiveController

Voraussetzung:
  - Compilierte Contracts unter abi/<ContractName>.json (mit "abi" und "bytecode")
  - .env mit DEPLOYER_PRIVATE_KEY
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

CONFIG_PATH_ENV = os.getenv("FRONTEND_CONFIG_PATH")
if CONFIG_PATH_ENV:
    CONFIG_PATH = Path(CONFIG_PATH_ENV)
else:
    CONFIG_PATH = Path(__file__).parent.parent / "config.json" # Fallback auf python/config.json

ABI_DIR = Path(__file__).parent.parent / "abi" # Zeigt auf python/abi/


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


def deploy(contract_name: str, constructor_args: list):
    """Deployed einen Contract und gibt die Adresse zurück."""
    artifact_path = ABI_DIR / f"{contract_name}.json"
    if not artifact_path.exists():
        print(f"FEHLER: {artifact_path} fehlt. Erst compilieren!")
        sys.exit(1)

    with open(artifact_path) as f:
        artifact = json.load(f)

    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])

    print(f"\nDeploye {contract_name} ...")
    print(f"  Deployer: {account.address}")
    print(f"  Args: {constructor_args}")

    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract.constructor(*constructor_args).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "chainId": bc["chain_id"],
        "gas": 4_000_000,
        "maxFeePerGas": w3.to_wei("30", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
    })
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX: {tx_hash.hex()}")
    print(f"  Warte auf Confirmation ...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    address = receipt.contractAddress
    print(f"  ✓ Deployed: {address}")
    print(f"  Etherscan: https://sepolia.etherscan.io/address/{address}\n")
    return address


import json
import os
from pathlib import Path

def update_config(contract_key: str, address: str):
    """Updates the specified contract address in config.json directly."""
    # Resolve config path via environment variable or default fallback
    config_path_env = os.getenv("FRONTEND_CONFIG_PATH")
    if config_path_env:
        config_path = Path(config_path_env).resolve()
    else:
        config_path = Path(__file__).parent.parent / "config.json"

    if not config_path.exists():
        print(f"⚠️  Warning: Config file not found at {config_path}")
        return

    try:
        # Read current config
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)

        # Ensure nested dict exists
        if "blockchain" not in config_data:
            config_data["blockchain"] = {}

        # Update specific address key
        config_data["blockchain"][contract_key] = address

        # Write back to disk
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        print(f"✅ Automatically updated blockchain.{contract_key} in {config_path}")

    except Exception as e:
        print(f"❌ Failed to update config.json: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    contract_name = sys.argv[1]
    args = sys.argv[2:]

    # Bring address arguments into checksum format
    constructor_args = [
        Web3.to_checksum_address(a) if a.startswith("0x") and len(a) == 42 else a
        for a in args
    ]

    address = deploy(contract_name, constructor_args)

    key_map = {
        "OracleStorage": "oracle_storage_address",
        "P2PEnergyMarket": "p2p_market_address",
        "BatteryManager": "battery_manager_address",
        "IncentiveController": "incentive_controller_address",
    }

    if contract_name in key_map:
        config_key = key_map[contract_name]
        print(f"→ Deployed {contract_name} at: {address}")
        
        # Save address directly into config.json
        update_config(config_key, address)


if __name__ == "__main__":
    main()