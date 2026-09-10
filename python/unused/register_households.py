#!/usr/bin/env python3
"""
register_households.py
-----------------------
Registriert Haushalte im P2PEnergyMarket-Contract via registerHousehold().

Das ist NICHT dasselbe wie approve_stablecoin.py:
  - approve_stablecoin.py  -> jeder Haushalt erlaubt dem Market-Contract,
                               Stablecoin in seinem Namen zu bewegen (ERC-20 approve)
  - register_households.py -> NUR der Owner darf Haushalte im Market registrieren
                               (onlyOwner), Voraussetzung: oracle.isHouseholdRegistered()
                               muss für die Adresse bereits true sein.

Private Key wird NICHT gespeichert, sondern interaktiv und verdeckt (getpass)
abgefragt - nur der Owner-Key wird gebraucht, nicht die Keys der Haushalte.

Nutzung:
    pip install web3
    python register_households.py
    python register_households.py --config ../config.json

Adressen-Quelle (in dieser Reihenfolge):
    1. "households"-Liste in config.json, falls vorhanden:
       { "households": [ {"id": "christian", "address": "0x..."}, ... ] }
    2. Sonst: interaktive Eingabe, eine Adresse pro Zeile, leere Zeile beendet.
"""

import argparse
import getpass
import json
import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import TimeExhausted

SEPOLIA_EXPLORER_TX = "https://sepolia.etherscan.io/tx/{}"
SEPOLIA_EXPLORER_ADDR = "https://sepolia.etherscan.io/address/{}"

# ─────────────────────────────────────────────────────────────
#  Minimales ABI (nur was wir brauchen)
# ─────────────────────────────────────────────────────────────

MARKET_ABI = [
    {
        "inputs": [{"name": "household", "type": "address"}],
        "name": "registerHousehold",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "", "type": "address"}],
        "name": "isRegistered",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ─────────────────────────────────────────────────────────────
#  Config laden
# ─────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        sys.exit(f"[Fehler] config.json nicht gefunden unter: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    bc = cfg.get("blockchain")
    if not bc:
        sys.exit("[Fehler] config.json enthält keinen 'blockchain'-Block.")

    required = ["rpc_url", "chain_id", "p2p_market_address"]
    missing = [k for k in required if not bc.get(k)]
    if missing:
        sys.exit(f"[Fehler] Folgende Felder fehlen in config.json -> blockchain: {missing}")

    if "REPLACE" in bc["p2p_market_address"].upper():
        sys.exit("[Fehler] 'p2p_market_address' in config.json ist noch ein Platzhalter.")

    return cfg


def load_household_addresses(cfg: dict) -> list[dict]:
    """Liest Haushalte aus config.json['households'], falls vorhanden, sonst interaktiv."""
    households = cfg.get("households")
    if households:
        result = []
        for h in households:
            addr = h.get("address")
            if not addr:
                continue
            result.append({"id": h.get("id", addr), "address": addr})
        if result:
            print(f"{len(result)} Haushalt(e) aus config.json übernommen.")
            return result

    print("\nKeine 'households'-Liste in config.json gefunden.")
    print("Adressen manuell eingeben (eine pro Zeile). Leere Eingabe beendet die Liste.\n")
    result = []
    while True:
        idx = len(result) + 1
        addr = input(f"  Adresse Haushalt #{idx} (oder Enter zum Beenden): ").strip()
        if not addr:
            break
        try:
            addr = Web3.to_checksum_address(addr)
        except Exception:
            print("    [Fehler] Ungültige Adresse, wird übersprungen.")
            continue
        result.append({"id": addr, "address": addr})
    return result


# ─────────────────────────────────────────────────────────────
#  Registrierung
# ─────────────────────────────────────────────────────────────

def register_household(
    w3: Web3,
    market,
    owner_account: Account,
    household_address: str,
    nonce: int,
    chain_id: int,
) -> bool:
    """Registriert einen Haushalt. Gibt True zurück, wenn eine Tx gesendet wurde
    (also der Nonce für den nächsten Aufruf erhöht werden muss)."""

    if market.functions.isRegistered(household_address).call():
        print("    Bereits registriert, überspringe.")
        return False

    gas_price = int(w3.eth.gas_price * 1.2)

    tx = market.functions.registerHousehold(household_address).build_transaction(
        {
            "chainId": chain_id,
            "from": owner_account.address,
            "nonce": nonce,
            "gasPrice": gas_price,
        }
    )
    try:
        estimated_gas = w3.eth.estimate_gas(tx)
        tx["gas"] = int(estimated_gas * 1.2)
    except Exception as e:
        # Wenn die Schätzung selbst schon revertiert, meist "Not in oracle" o.ä.
        print(f"    [Fehler] Gas-Schätzung fehlgeschlagen (würde vermutlich revertieren): {e}")
        return False

    signed_tx = owner_account.sign_transaction(tx)
    raw_tx = getattr(signed_tx, "raw_transaction", None) or signed_tx.rawTransaction

    tx_hash = w3.eth.send_raw_transaction(raw_tx)
    tx_hash_hex = tx_hash.hex() if tx_hash.hex().startswith("0x") else "0x" + tx_hash.hex()
    print(f"    Tx gesendet: {tx_hash_hex}")
    print(f"    Explorer:    {SEPOLIA_EXPLORER_TX.format(tx_hash_hex)}")
    print(f"    warte auf Bestätigung (Timeout 180s) ...")

    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180, poll_latency=2)
    except TimeExhausted:
        print(
            "    ⏳ Noch nicht bestätigt nach 180s. Status im Explorer prüfen: "
            f"{SEPOLIA_EXPLORER_TX.format(tx_hash_hex)}"
        )
        return True

    if receipt.status == 1:
        print("    ✓ Erfolgreich registriert.")
    else:
        print(f"    ✗ Transaktion fehlgeschlagen (Status 0). Tx-Hash: {tx_hash_hex}")

    return True


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Haushalte im P2PEnergyMarket registrieren (nur Owner)")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "config.json",
        help="Pfad zur config.json (Default: ../config.json relativ zu diesem Skript)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    bc = cfg["blockchain"]

    print(f"Verbinde mit RPC: {bc['rpc_url']} (chainId={bc['chain_id']})")
    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    if not w3.is_connected():
        sys.exit("[Fehler] Verbindung zum RPC-Endpoint fehlgeschlagen.")

    market_address = Web3.to_checksum_address(bc["p2p_market_address"])
    market = w3.eth.contract(address=market_address, abi=MARKET_ABI)

    on_chain_owner = market.functions.owner().call()
    print(f"P2PEnergyMarket: {market_address}")
    print(f"Contract-Owner:  {on_chain_owner}")

    households = load_household_addresses(cfg)
    if not households:
        sys.exit("\nKeine Haushalte zum Registrieren - nichts zu tun.")

    print(f"\n{len(households)} Haushalt(e) zu registrieren:")
    for h in households:
        print(f"  - {h['id']}: {h['address']}")

    owner_key = getpass.getpass("\nPrivate Key des Owners eingeben (verdeckt): ").strip()
    if not owner_key.startswith("0x"):
        owner_key = "0x" + owner_key
    try:
        owner_account = Account.from_key(owner_key)
    except Exception as e:
        sys.exit(f"[Fehler] Ungültiger Private Key: {e}")
    finally:
        owner_key = "0" * 64

    if owner_account.address.lower() != on_chain_owner.lower():
        print(
            f"\n⚠️  Warnung: der eingegebene Key gehört zu {owner_account.address}, "
            f"aber der Contract-Owner ist {on_chain_owner}. "
            "registerHousehold() wird mit 'Only owner' revertieren."
        )
        proceed = input("Trotzdem fortfahren? (y/N): ").strip().lower()
        if proceed != "y":
            sys.exit("Abgebrochen.")

    nonce = w3.eth.get_transaction_count(owner_account.address, "pending")

    print(f"\nStarte Registrierung ...\n")
    for h in households:
        print(f"[{h['id']}] {h['address']}")
        try:
            sent = register_household(
                w3=w3,
                market=market,
                owner_account=owner_account,
                household_address=Web3.to_checksum_address(h["address"]),
                nonce=nonce,
                chain_id=bc["chain_id"],
            )
            if sent:
                nonce += 1
        except Exception as e:
            print(f"    [Fehler] {e}")
        print()

    print("Fertig.")


if __name__ == "__main__":
    main()
 
 