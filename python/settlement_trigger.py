"""
settlement_trigger.py

Ruft jede Simulationsminute settleSlot() auf dem P2PEnergyMarket-Contract auf.
Damit wird der vom Oracle gefütterte Slot abgerechnet:
  - Produzenten erhalten Stablecoins
  - Konsumenten zahlen Stablecoins

Voraussetzung:
  - .env mit TRIGGER_PRIVATE_KEY (kann derselbe Key wie DEPLOYER sein)
  - config.json mit p2p_market_address gesetzt
  - Konsumenten müssen vorher approve() auf den Stablecoin aufgerufen haben

Unterschiede zum Starter-Code (Phase 1):
  - settleSlot() braucht weiterhin KEINE Argumente, der TODO-Block entfällt
  - Vorprüfung: es wird nur gesendet, wenn der Oracle wirklich einen neuen Slot
    hat. Der Contract revertet sonst mit "Slot already settled" - das kostet
    Gas für nichts. Auf Sepolia läuft currentSlot nach block.timestamp und
    kann springen, deshalb wird der Slot gelesen statt mitgezählt.
  - Dry-Run per eth_call vor dem Senden: Reverts werden erkannt, bevor eine
    Transaktion bezahlt wird
  - Gas wird geschätzt statt fix gesetzt (Puffer 50%); der feste Wert von
    1_500_000 wird zu knapp, sobald BatteryManager/IncentiveController
    verknüpft sind und pro Haushalt externe Calls dazukommen
  - Die Events des Contracts werden ausgewertet und angezeigt
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.logs import DISCARD

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"
ABI_DIR = Path(__file__).parent / "abi"

PRIVATE_KEY = os.getenv("TRIGGER_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
if not PRIVATE_KEY:
    print("ERROR: TRIGGER_PRIVATE_KEY in .env nicht gesetzt")
    sys.exit(1)

# Nur der eine Getter, den wir vom Oracle brauchen - so muss die volle
# OracleStorage-ABI hier nicht geladen werden.
ORACLE_MINIMAL_ABI = [{
    "inputs": [],
    "name": "getCurrentSlot",
    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
    "stateMutability": "view",
    "type": "function",
}]

# Stablecoin nutzt 6 Decimals (siehe README).
TOKEN_DECIMALS = 6


def fmt_token(units: int) -> str:
    """Token-Units in lesbare Token umrechnen."""
    return f"{units / 10 ** TOKEN_DECIMALS:.4f}"


def log_settlement_events(market, receipt):
    """Wertet die Events eines erfolgreichen settleSlot()-Aufrufs aus."""
    for ev in market.events.EnergyTraded().process_receipt(receipt, errors=DISCARD):
        a = ev["args"]
        print(f"    Handel: {a['energyWh']:>6} Wh  "
              f"{a['producer'][:8]}... -> {a['consumer'][:8]}...  "
              f"{fmt_token(a['amountPaid'])} Token")

    # Konsumenten ohne approve() oder ohne Guthaben. Kein Fehler im Contract,
    # aber praktisch immer ein Setup-Problem -> deshalb sichtbar machen.
    for ev in market.events.ConsumerSkipped().process_receipt(receipt, errors=DISCARD):
        a = ev["args"]
        print(f"    ! Uebersprungen: {a['consumer']} "
              f"(braucht {fmt_token(a['requestedAmount'])} Token "
              f"-> approve() und Guthaben pruefen)")

    # Nur relevant, sobald der BatteryManager verknuepft ist (Phase 2).
    for ev in market.events.BatteryDecisionFailed().process_receipt(receipt, errors=DISCARD):
        print(f"    ! Batterie-Call fehlgeschlagen: {ev['args']['household']}")

    for ev in market.events.SlotSettled().process_receipt(receipt, errors=DISCARD):
        a = ev["args"]
        print(f"    Summe Slot {a['slot']}: {a['totalEnergyTraded']} Wh, "
              f"{fmt_token(a['totalPaid'])} Token")


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
    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(bc["oracle_storage_address"]),
        abi=ORACLE_MINIMAL_ABI
    )

    slot_seconds = config.get("simulation", {}).get("slot_duration_seconds", 60)

    print(f"Settlement-Trigger gestartet, Account: {account.address}")
    print(f"Market-Contract: {bc['p2p_market_address']}")
    print(f"Oracle-Contract: {bc['oracle_storage_address']}\n")

    while True:
        try:
            # ── Vorpruefung: gibt es ueberhaupt einen neuen Slot? ──────
            current_slot = oracle.functions.getCurrentSlot().call()
            last_settled = market.functions.lastSettledSlot().call()

            if current_slot <= last_settled:
                print(f"  Slot {current_slot} bereits abgerechnet - warte "
                      f"(Oracle laeuft evtl. noch nicht oder haengt zurueck)")
                time.sleep(slot_seconds)
                continue

            print(f"\n-> settleSlot() fuer Slot {current_slot} "
                  f"um {time.strftime('%H:%M:%S')}")

            # ── Dry-Run: Revert erkennen, bevor Gas bezahlt wird ───────
            market.functions.settleSlot().call({"from": account.address})

            # ── Gas schaetzen statt fix setzen ─────────────────────────
            gas_estimate = market.functions.settleSlot().estimate_gas(
                {"from": account.address}
            )
            gas_limit = int(gas_estimate * 1.5)

            nonce = w3.eth.get_transaction_count(account.address, "pending")
            tx = market.functions.settleSlot().build_transaction({
                "from": account.address,
                "nonce": nonce,
                "chainId": bc["chain_id"],
                "gas": gas_limit,
                "maxFeePerGas": w3.to_wei("30", "gwei"),
                "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
            })
            signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"  TX: {tx_hash.hex()}")

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                print(f"  OK Settlement erfolgreich (Block {receipt.blockNumber}, "
                      f"Gas {receipt.gasUsed}/{gas_limit})")
                log_settlement_events(market, receipt)
            else:
                # Sollte nach dem Dry-Run kaum vorkommen - moeglich, wenn sich
                # der State zwischen Dry-Run und Mining aendert (z.B. ein
                # zweiter Trigger war schneller).
                print("  FEHLER Settlement fehlgeschlagen (Status 0)")

        except KeyboardInterrupt:
            print("\nSettlement-Trigger gestoppt.")
            break
        except Exception as e:
            # Haeufigste Faelle: "Slot already settled" (zweiter Trigger war
            # schneller) oder RPC-Timeout. Beides ist kein Abbruchgrund.
            print(f"  Fehler: {e}")

        time.sleep(slot_seconds)


if __name__ == "__main__":
    main()
