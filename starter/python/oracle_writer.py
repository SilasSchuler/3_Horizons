"""
oracle_writer.py

VOLLSTÄNDIG VORGEGEBEN - Teams modifizieren dieses Skript NICHT.

Liest Daten vom EnergySimulator und schreibt sie jeden Slot in den
OracleStorage Smart Contract auf Sepolia.

Verantwortlichkeiten:
  - Slot-Counter im Oracle aktualisieren
  - Pro Haushalt: Meter- und Batteriedaten on-chain schreiben
  - Wetterdaten on-chain schreiben
  - Nonce-Management & Retry bei Gas-Problemen

Hinweis zum Timing (bekannte Einschränkung, kein Bug in eurem Contract-Code):
  Pro Slot werden hier bis zu 8 sequenzielle Transaktionen gesendet
  (updateSlot, updateWeather, pro Haushalt updateMeter + updateBattery).
  Bei ~12s Blockzeit auf Sepolia kann ein Durchlauf locker die Ziel-Slotdauer
  von 60s überschreiten. `OracleStorage.currentSlot` läuft nach
  `block.timestamp`, nicht nach Anzahl `updateSlot()`-Aufrufen - er kann also
  auch mal Sprünge machen, wenn ein Durchlauf länger als 60s dauert. Plant
  eure Contract-Logik (v.a. settleSlot()) so, dass sie nicht auf exakte
  60-Sekunden-Abstände zwischen Slots angewiesen ist.

  Zusätzlich: Die simulierte Tageszeit und der Batterie-SoC leben nur im
  Prozessspeicher des EnergySimulator (siehe data_simulator.py). Bei jedem
  Neustart dieses Skripts (z.B. beim Debuggen) beginnt die simulierte Uhrzeit
  wieder bei 0 und der SoC wieder bei 50% - das ist erwartetes Verhalten,
  kein Fehler in eurem Contract-Code.

Voraussetzung:
  - .env mit ORACLE_PRIVATE_KEY (Wallet, die als autorisierter Oracle eingetragen ist)
  - config.json mit deployten Contract-Adressen
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from data_simulator import EnergySimulator

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"
ABI_DIR = Path(__file__).parent / "abi"

ORACLE_PRIVATE_KEY = os.getenv("ORACLE_PRIVATE_KEY")
if not ORACLE_PRIVATE_KEY:
    print("ERROR: ORACLE_PRIVATE_KEY in .env nicht gesetzt")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────

class OracleWriter:
    """
    Schreibt simulierte Daten on-chain.
    """

    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = json.load(f)

        bc = self.config["blockchain"]
        self.w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
        # Manche Sepolia-RPCs liefern PoA-extra-data
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self.account = self.w3.eth.account.from_key(ORACLE_PRIVATE_KEY)
        print(f"Oracle Account: {self.account.address}")
        balance_eth = self.w3.from_wei(self.w3.eth.get_balance(self.account.address), "ether")
        print(f"Sepolia ETH Balance: {balance_eth}")

        # Contract laden
        with open(ABI_DIR / "OracleStorage.json") as f:
            oracle_abi = json.load(f)["abi"]
        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(bc["oracle_storage_address"]),
            abi=oracle_abi
        )

        self.simulator = EnergySimulator(CONFIG_PATH)
        self.chain_id = bc["chain_id"]

    # ─────────────────────────────────────────────────────────────

    def _send_tx(self, contract_function, max_retries: int = 3):
        """Baut, signiert, sendet eine Transaktion - mit Retry und Nonce-Management."""
        for attempt in range(max_retries):
            try:
                nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")
                tx = contract_function.build_transaction({
                    "from": self.account.address,
                    "nonce": nonce,
                    "chainId": self.chain_id,
                    "gas": 300_000,
                    "maxFeePerGas": self.w3.to_wei("30", "gwei"),
                    "maxPriorityFeePerGas": self.w3.to_wei("2", "gwei"),
                })
                signed = self.w3.eth.account.sign_transaction(tx, ORACLE_PRIVATE_KEY)
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                return receipt
            except Exception as e:
                print(f"  TX fehlgeschlagen (Versuch {attempt+1}): {e}")
                time.sleep(5)
        raise RuntimeError("Max retries erreicht")

    # ─────────────────────────────────────────────────────────────

    def register_households_if_needed(self):
        """Stellt sicher, dass alle konfigurierten Haushalte im Oracle registriert sind."""
        for h in self.config["households"]:
            addr = Web3.to_checksum_address(h["address"])
            registered = self.oracle.functions.isHouseholdRegistered(addr).call()
            if not registered:
                print(f"Registriere Haushalt {h['id']} ({addr}) ...")
                self._send_tx(self.oracle.functions.registerHousehold(addr))

    # ─────────────────────────────────────────────────────────────

    def push_slot(self):
        """Liest Simulator-Daten und schreibt sie als kompletten Slot on-chain."""
        data = self.simulator.get_current_readings()

        # 1. Slot-Counter aktualisieren
        print(f"\n→ Slot {time.strftime('%H:%M:%S')} (Sim-h={data['sim_hour']:.2f})")
        self._send_tx(self.oracle.functions.updateSlot())

        # 2. Wetterdaten schreiben
        w = data["weather"]
        self._send_tx(self.oracle.functions.updateWeather(
            w["irradiance_wm2"],
            w["temperature_c_x10"],
            w["cloud_cover"]
        ))
        print(f"  Wetter: {w['irradiance_wm2']} W/m², {w['cloud_cover']}% Wolken")

        # 3. Pro Haushalt: Meter und Battery
        for h in data["households"]:
            addr = Web3.to_checksum_address(h["address"])
            self._send_tx(self.oracle.functions.updateMeter(
                addr,
                h["consumption_wh"],
                h["production_wh"]
            ))
            if h["battery_capacity_wh"] > 0:
                self._send_tx(self.oracle.functions.updateBattery(
                    addr,
                    h["battery_soc"],
                    h["battery_capacity_wh"],
                    h["battery_max_rate_wh"]
                ))
            print(f"  {h['household_id']}: "
                  f"V={h['consumption_wh']}Wh, P={h['production_wh']}Wh, "
                  f"SoC={h['battery_soc']}%")

    # ─────────────────────────────────────────────────────────────

    def run(self, slot_seconds: int = 60):
        """Hauptschleife: pushe einen Slot pro Minute."""
        print("\n=== Oracle Writer gestartet ===\n")
        self.register_households_if_needed()
        try:
            while True:
                start = time.time()
                self.push_slot()
                elapsed = time.time() - start
                wait = max(0, slot_seconds - elapsed)
                print(f"  (warte {wait:.0f}s bis nächster Slot)")
                time.sleep(wait)
        except KeyboardInterrupt:
            print("\nOracle Writer gestoppt.")


# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    writer = OracleWriter()
    writer.run()
