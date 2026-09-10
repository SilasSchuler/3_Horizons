"""
setup_phase3.py

Einmaliges On-Chain-Setup fuer Phase 3, nach dem Deployment von
IncentiveController und dem neu deployten P2PEnergyMarket.

  1. Autorisiert die Adresse, die Prognosen einreichen darf
  2. Verknuepft den IncentiveController im Market (setIncentiveController)
  3. Zeigt zur Kontrolle Score und Preismultiplikator je Haushalt

Wichtig: oracle_writer.py waehrend dieses Skripts STOPPEN. Beide senden von
derselben Adresse und kollidieren sonst bei der Nonce.
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

    for key in ("incentive_controller_address", "p2p_market_address"):
        if "REPLACE" in bc.get(key, "REPLACE"):
            print(f"ERROR: {key} in config.json ist nicht gesetzt")
            sys.exit(1)

    deployer_key = os.getenv("DEPLOYER_PRIVATE_KEY")
    if not deployer_key:
        print("ERROR: DEPLOYER_PRIVATE_KEY in .env nicht gesetzt")
        sys.exit(1)

    # Die Adresse, die spaeter Prognosen einreicht. Faellt auf den Deployer
    # zurueck, wenn kein eigener Schluessel hinterlegt ist.
    ai_key = os.getenv("AI_PRIVATE_KEY") or deployer_key

    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    deployer = w3.eth.account.from_key(deployer_key)
    ai_account = w3.eth.account.from_key(ai_key)

    with open(ABI_DIR / "P2PEnergyMarket.json") as f:
        market_abi = json.load(f)["abi"]
    with open(ABI_DIR / "IncentiveController.json") as f:
        ic_abi = json.load(f)["abi"]

    market = w3.eth.contract(
        address=Web3.to_checksum_address(bc["p2p_market_address"]), abi=market_abi)
    ic = w3.eth.contract(
        address=Web3.to_checksum_address(bc["incentive_controller_address"]),
        abi=ic_abi)

    print(f"Market:              {market.address}")
    print(f"IncentiveController: {ic.address}")
    print(f"Deployer:            {deployer.address}")
    print(f"AI-Submitter:        {ai_account.address}")

    # ── 1. Prognose-Einreicher autorisieren ───────────────────────
    print("\nAutorisierung:")
    if ic.functions.authorizedAI(ai_account.address).call():
        print("    Bereits autorisiert")
    else:
        send(w3, bc, ic.functions.authorizeAI(ai_account.address),
             deployer_key, deployer, "authorizeAI()")

    # ── 2. Controller im Market verknuepfen ───────────────────────
    print("\nVerknuepfung im Market:")
    aktuell = market.functions.incentiveController().call()
    if aktuell.lower() == ic.address.lower():
        print("    Bereits verknuepft")
    else:
        send(w3, bc, market.functions.setIncentiveController(ic.address),
             deployer_key, deployer, "setIncentiveController()")

    # ── 3. Kontrolle ──────────────────────────────────────────────
    # Ohne Prognose-Historie ist der Score 0 und der Multiplikator neutral.
    # Das ist Absicht: Ein neuer Teilnehmer soll nicht bestraft werden.
    print("\nAktueller Stand je Haushalt:")
    print("  Haushalt    Score  Multiplikator  Preis fuer 1 kWh")
    for h in config["households"]:
        addr = Web3.to_checksum_address(h["address"])
        score = ic.functions.getReputationScore(addr).call()
        mult = ic.functions.getPriceMultiplier(addr).call()
        kosten = market.functions.calculateCostFor(addr, 1000).call()
        print(f"  {h['id']:10s}  {score:5d}  {mult:13d}  {kosten:>10d}")

    basis = market.functions.calculateCost(1000).call()
    print(f"\n  Basispreis ohne Incentive: {basis}")
    print("\nFertig. Sobald Prognosen eingereicht werden, weichen die Preise ab.")


if __name__ == "__main__":
    main()