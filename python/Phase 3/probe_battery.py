"""
probe_battery.py

Einmaliges Hilfsskript: liest aus, welche Funktionen und Events der
BatteryManager anbietet und wie ein DecisionMade-Event aussieht.

Damit weiss ich, welche Daten sich fuer die Speicher-Darstellung im
Dashboard verwenden lassen. Danach kann die Datei geloescht werden.

Aufruf aus dem Ordner "Phase 3":
    python probe_battery.py
"""

import json
from pathlib import Path

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HIER = Path(__file__).parent
CONFIG_PATH = HIER.parent / "config.json"
ABI_DIR = HIER.parent / "abi"


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    bc = config["blockchain"]

    with open(ABI_DIR / "BatteryManager.json") as f:
        abi = json.load(f)["abi"]

    print("=== Funktionen ===")
    for e in abi:
        if e.get("type") == "function":
            args = ", ".join(f"{i['type']} {i.get('name','')}".strip()
                             for i in e.get("inputs", []))
            outs = ", ".join(o["type"] for o in e.get("outputs", []))
            print(f"  {e['name']}({args})" + (f" -> {outs}" if outs else ""))

    print("\n=== Events ===")
    for e in abi:
        if e.get("type") == "event":
            args = ", ".join(f"{i['type']} {i['name']}"
                             for i in e.get("inputs", []))
            print(f"  {e['name']}({args})")

    # ── Aktueller Stand je Haushalt ───────────────────────────────
    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    bm = w3.eth.contract(
        address=Web3.to_checksum_address(bc["battery_manager_address"]),
        abi=abi)

    print("\n=== Aktueller Stand ===")
    for h in config["households"]:
        adresse = Web3.to_checksum_address(h["address"])
        zeile = [h["id"]]
        for name in ("batteries", "getBattery", "getBatteryState",
                     "getSoC", "socOf", "batteryOf"):
            try:
                fn = getattr(bm.functions, name)
                zeile.append(f"{name}={fn(adresse).call()}")
                break
            except Exception:
                continue
        print("  " + "  ".join(str(x) for x in zeile))

    # ── Ein paar Events ansehen ───────────────────────────────────
    latest = w3.eth.block_number
    print(f"\n=== DecisionMade-Events (letzte 2000 Bloecke ab {latest}) ===")
    gefunden = 0
    b = latest - 2000
    while b <= latest and gefunden < 5:
        e = min(b + 800, latest)
        try:
            for ev in bm.events.DecisionMade.get_logs(from_block=b, to_block=e):
                print(f"  Block {ev['blockNumber']}: {dict(ev['args'])}")
                gefunden += 1
                if gefunden >= 5:
                    break
        except Exception as err:
            print(f"  (Fehler: {type(err).__name__})")
            break
        b = e + 1
    if gefunden == 0:
        print("  keine gefunden")


if __name__ == "__main__":
    main()