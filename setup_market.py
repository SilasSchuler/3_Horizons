"""
setup_market.py

Einmaliges On-Chain-Setup nach jedem Deployment von P2PEnergyMarket.

Was das Skript macht:
  1. Prueft, ob die Haushalte aus config.json im OracleStorage registriert sind
  2. Registriert sie im P2PEnergyMarket (nur der Owner darf das)
  3. Setzt fuer jeden Haushalt, dessen Private Key in .env liegt, die
     Stablecoin-Allowance auf den Market und holt bei Bedarf Tokens vom Faucet

Warum das noetig ist:
  registerHousehold() und approve() haengen an der Contract-Adresse. Nach einem
  Neu-Deployment gilt beides fuer den alten Contract, nicht fuer den neuen.

Voraussetzung:
  - config.json mit der NEUEN p2p_market_address
  - .env mit DEPLOYER_PRIVATE_KEY (muss der Owner des Market sein)
  - Fuer approve(): pro Haushalt ein Key in .env, benannt nach der id aus
    config.json in Grossbuchstaben, z.B.
        HOUSE_01_PRIVATE_KEY=0x...
        HOUSE_02_PRIVATE_KEY=0x...
        HOUSE_03_PRIVATE_KEY=0x...
    Fehlt ein Key, wird der Haushalt uebersprungen und am Ende aufgelistet -
    approve() muss dann sein Besitzer selbst ausfuehren (z.B. ueber MetaMask).

  Haushalte im Oracle registriert oracle_writer.py beim Start automatisch.
  Lass das Skript also einmal kurz laufen, bevor du hier startest.
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

# Unbegrenzte Allowance. Nur auf einem Testnet vertretbar - im Mainnet wuerde
# man einen realistischen Betrag setzen und bei Bedarf nachziehen.
MAX_ALLOWANCE = 2 ** 256 - 1

# Nur die Funktionen, die hier gebraucht werden - so muss keine volle
# Stablecoin-ABI im Repo liegen.
STABLECOIN_ABI = [
    {"inputs": [{"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "faucet", "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
]


def send(w3, bc, fn, key, account, label):
    """Baut, signiert und sendet eine Transaktion. Gibt True bei Erfolg."""
    try:
        nonce = w3.eth.get_transaction_count(account.address, "pending")
        tx = fn.build_transaction({
            "from": account.address,
            "nonce": nonce,
            "chainId": bc["chain_id"],
            "gas": int(fn.estimate_gas({"from": account.address}) * 1.5),
            "maxFeePerGas": w3.to_wei("30", "gwei"),
            "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
        })
        signed = w3.eth.account.sign_transaction(tx, key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status == 1:
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

    if "REPLACE" in bc["p2p_market_address"]:
        print("ERROR: p2p_market_address in config.json ist nicht gesetzt")
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
    with open(ABI_DIR / "OracleStorage.json") as f:
        oracle_abi = json.load(f)["abi"]

    market = w3.eth.contract(
        address=Web3.to_checksum_address(bc["p2p_market_address"]), abi=market_abi)
    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(bc["oracle_storage_address"]), abi=oracle_abi)
    token = w3.eth.contract(
        address=Web3.to_checksum_address(bc["stablecoin_address"]), abi=STABLECOIN_ABI)

    print(f"Market:   {market.address}")
    print(f"Oracle:   {oracle.address}")
    print(f"Deployer: {deployer.address}")

    # Sanity-Check: nur der Owner darf registrieren.
    owner = market.functions.owner().call()
    if owner.lower() != deployer.address.lower():
        print(f"\nERROR: Owner des Market ist {owner}, nicht der Deployer.")
        print("Zeigt p2p_market_address noch auf ein altes Deployment?")
        sys.exit(1)

    missing_keys = []
    market_addr = Web3.to_checksum_address(bc["p2p_market_address"])

    for h in config["households"]:
        addr = Web3.to_checksum_address(h["address"])
        print(f"\n{h['id']}  {addr}")

        # ── 1. Oracle-Registrierung pruefen ────────────────────────────
        if not oracle.functions.isHouseholdRegistered(addr).call():
            print("    Nicht im Oracle registriert -> uebersprungen.")
            print("    Starte zuerst oracle_writer.py, der registriert automatisch.")
            continue

        # ── 2. Im Market registrieren ─────────────────────────────────
        if market.functions.isRegistered(addr).call():
            print("    Bereits im Market registriert")
        else:
            send(w3, bc, market.functions.registerHousehold(addr),
                 deployer_key, deployer, "registerHousehold()")

        # ── 3. approve() - braucht den Key des Haushalts selbst ────────
        env_name = f"{h['id'].upper()}_PRIVATE_KEY"
        h_key = os.getenv(env_name)
        if not h_key:
            missing_keys.append((h["id"], addr, env_name))
            print(f"    approve() uebersprungen ({env_name} fehlt in .env)")
            continue

        h_acc = w3.eth.account.from_key(h_key)
        if h_acc.address.lower() != addr.lower():
            print(f"    WARNUNG: {env_name} gehoert zu {h_acc.address}, "
                  f"nicht zu {addr} -> uebersprungen")
            continue

        if token.functions.balanceOf(addr).call() == 0:
            print("    Kein Token-Guthaben -> faucet()")
            send(w3, bc, token.functions.faucet(), h_key, h_acc, "faucet()")

        current = token.functions.allowance(addr, market_addr).call()
        if current > 10 ** 12:
            print("    Allowance bereits gesetzt")
        else:
            send(w3, bc, token.functions.approve(market_addr, MAX_ALLOWANCE),
                 h_key, h_acc, "approve()")

    if missing_keys:
        print("\n" + "=" * 60)
        print("Fuer diese Haushalte fehlt approve(). Ohne das werden sie in")
        print("settleSlot() uebersprungen (Event ConsumerSkipped).")
        print("Entweder den Key in .env eintragen und dieses Skript erneut")
        print("laufen lassen, oder der Besitzer ruft approve() selbst auf:\n")
        for hid, addr, env_name in missing_keys:
            print(f"  {hid}  {addr}")
            print(f"      .env-Eintrag: {env_name}=0x...")
        print(f"\n  approve(spender={market_addr}, amount=2**256-1)")
        print(f"  auf Token {bc['stablecoin_address']}")
        print("=" * 60)

    print("\nFertig. Naechster Schritt: oracle_writer.py und settlement_trigger.py "
          "in zwei Terminals starten.")


if __name__ == "__main__":
    main()
