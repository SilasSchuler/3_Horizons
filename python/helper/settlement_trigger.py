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
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.logs import DISCARD
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()

CONFIG_PATH_ENV = os.getenv("FRONTEND_CONFIG_PATH")
if CONFIG_PATH_ENV:
    CONFIG_PATH = Path(CONFIG_PATH_ENV)
else:
    CONFIG_PATH = Path(__file__).parent.parent / "config.json"  # Fallback auf python/config.json

ABI_DIR = Path(__file__).parent.parent / "abi"  # Zeigt auf python/abi/

PRIVATE_KEY = os.getenv("TRIGGER_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
if not PRIVATE_KEY:
    print("ERROR: TRIGGER_PRIVATE_KEY in .env nicht gesetzt")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────


def log_settlement_events(market, receipt, slot_hint: str):
    """Decode and log EnergyTraded + SlotSettled events from a settleSlot() receipt."""
    trades = market.events.EnergyTraded().process_receipt(receipt, errors=DISCARD)
    for trade in trades:
        args = trade["args"]
        print(f"  TRADE slot={args['slot']} producer={args['producer']} "
              f"consumer={args['consumer']} energyWh={args['energyWh']} "
              f"amountPaid={args['amountPaid']}")

    settled = market.events.SlotSettled().process_receipt(receipt, errors=DISCARD)
    for s in settled:
        args = s["args"]
        print(f"  SLOT_SETTLED slot={args['slot']} "
              f"totalEnergyTraded={args['totalEnergyTraded']} "
              f"totalPaid={args['totalPaid']} tradeCount={len(trades)}")

    if not trades and not settled:
        print(f"  Keine EnergyTraded/SlotSettled Events im Receipt gefunden (slot={slot_hint})")


def neuer_slot_vorhanden(oracle, market):
    """
    Prüft ohne Transaktion, ob überhaupt etwas abzurechnen ist.

    Der Contract lehnt einen bereits abgerechneten Slot mit
    "Current slot must be greater than last settled slot" ab. Das ist
    korrekt, kostet aber trotzdem Gas, weil die Transaktion erst beim
    Ausführen scheitert. Der Oracle braucht für seine acht Transaktionen
    pro Slot mitunter länger als sechzig Sekunden - der Trigger überholt
    ihn dann regelmässig.

    Zwei Lesezugriffe kosten nichts und ersparen die Fehlschläge.

    Gibt (bereit, aktueller_slot, letzter_slot) zurück. Lässt sich der
    Zustand nicht ermitteln, wird bereit=True gemeldet und der Contract
    entscheidet - besser ein vergeblicher Versuch als ein verpasster Slot.
    """
    try:
        aktuell = oracle.functions.getCurrentSlot().call()
        letzter = market.functions.lastSettledSlot().call()
        return aktuell > letzter, aktuell, letzter
    except Exception as e:
        print(f"  Slot-Vorprüfung nicht möglich ({type(e).__name__}), "
              f"versuche es trotzdem")
        return True, None, None


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

    # Für die Vorprüfung. Fehlt die Oracle-Adresse oder das ABI, läuft der
    # Trigger wie bisher ohne Vorprüfung weiter.
    oracle = None
    try:
        with open(ABI_DIR / "OracleStorage.json") as f:
            oracle_abi = json.load(f)["abi"]
        oracle = w3.eth.contract(
            address=Web3.to_checksum_address(bc["oracle_storage_address"]),
            abi=oracle_abi
        )
    except Exception as e:
        print(f"Hinweis: Slot-Vorprüfung deaktiviert ({type(e).__name__}). "
              f"Der Trigger sendet wie bisher bei jedem Durchlauf.")

    print("\n=== Settlement Trigger gestartet ===\n")
    print(f"Account: {account.address}")
    print(f"Market-Contract: {bc['p2p_market_address']}")
    if oracle is not None:
        print(f"Oracle-Contract: {bc['oracle_storage_address']}")
        print("Slot-Vorprüfung aktiv: es wird nur gesendet, wenn ein neuer "
              "Slot vorliegt.")

    uebersprungen = 0

    try:
        while True:
            try:
                if oracle is not None:
                    bereit, aktuell, letzter = neuer_slot_vorhanden(oracle, market)
                    if not bereit:
                        uebersprungen += 1
                        print(f"\n· Kein neuer Slot @ {time.strftime('%H:%M:%S')} "
                              f"- Oracle steht bei {aktuell}, zuletzt abgerechnet "
                              f"{letzter}. Nichts zu tun, kein Gas verbraucht "
                              f"(bisher {uebersprungen}x übersprungen).")
                        print("  (warte 60s bis nächster Slot)")
                        time.sleep(60)
                        continue
                    if aktuell is not None:
                        print(f"\n→ Trigger settleSlot() @ {time.strftime('%H:%M:%S')} "
                              f"- Slot {letzter} bis {aktuell} offen")
                    else:
                        print(f"\n→ Trigger settleSlot() @ {time.strftime('%H:%M:%S')}")
                else:
                    print(f"\n→ Trigger settleSlot() @ {time.strftime('%H:%M:%S')}")

                nonce = w3.eth.get_transaction_count(account.address, "pending")
                tx = market.functions.settleSlot().build_transaction({
                    "from": account.address,
                    "nonce": nonce,
                    "chainId": bc["chain_id"],
                    "gas": 1_500_000,  # eventuell anpassen
                    "maxFeePerGas": w3.to_wei("30", "gwei"),
                    "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
                })
                signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                print(f"  TX: {tx_hash.hex()}")
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

                if receipt.status == 1:
                    print(f"  Settlement erfolgreich (Block {receipt.blockNumber})")
                    log_settlement_events(market, receipt, slot_hint=tx_hash.hex())
                else:
                    # Kann trotz Vorprüfung passieren: Zwischen Prüfung und
                    # Ausführung kann ein anderer Trigger denselben Slot
                    # abgerechnet haben.
                    print(f"  Settlement fehlgeschlagen! tx={tx_hash.hex()}")

            except ContractLogicError as e:
                print(f"  Contract hat abgelehnt: {e}")
            except Exception as e:
                print(f"  Fehler: {e}")

            print("  (warte 60s bis nächster Slot)")
            time.sleep(60)  # 1 Slot warten
    except KeyboardInterrupt:
        print("\nSettlement Trigger gestoppt.")
        if uebersprungen:
            print(f"{uebersprungen} Durchläufe ohne neuen Slot übersprungen, "
                  f"entsprechend kein Gas verschwendet.")


if __name__ == "__main__":
    main()