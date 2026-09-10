"""
visualize_battery.py

Liest die On-Chain-Events und stellt den Ladestand-Verlauf grafisch dar.
Erfuellt das Phase-2-Lieferobjekt "Visualisierung: Ladestand-Verlauf ueber Zeit".

Ausgabe:
  1. Ein Matplotlib-Fenster mit drei Panels (SoC, Energie, Handel)
  2. plots/battery_soc.png              - Grafik fuer Bericht/Slides
  3. plots/chart_data.json              - Rohdaten fuer eine HTML-Visualisierung

Gelesene Events:
  OracleStorage.BatteryUpdated(household, slot, soc)          -> SoC-Kurve
  OracleStorage.MeterUpdated(household, slot, cons, prod)     -> Energiefluss
  BatteryManager.DecisionMade(household, action, amount, ...) -> Lade-/Entladepunkte
  P2PEnergyMarket.SlotSettled(slot, energyTraded, paid)       -> Handelsvolumen

Aufruf:
    python visualize_battery.py                  # letzte 20000 Bloecke, letzter Lauf
    python visualize_battery.py 40000            # groesserer Blockbereich
    python visualize_battery.py 20000 --alles    # ohne Slot-Filter, alle Laeufe

Warum der Slot-Filter: Das Oracle wurde ueber mehrere Contract-Generationen
und mehrere Simulationslaeufe hinweg beschrieben. Die Slot-Nummern laufen nach
block.timestamp, ueberdecken also auch Zeitraeume, in denen gar nichts lief.
Ohne Filter besteht die Grafik zu zwei Dritteln aus Interpolation zwischen
weit auseinanderliegenden Punkten. Standardmaessig wird deshalb nur der letzte
zusammenhaengende Abschnitt gezeigt.

Hinweis: Es wird nur GELESEN, keine Transaktion gesendet. Kostet kein Gas und
braucht keinen Private Key.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
ABI_DIR = BASE / "abi"
PLOT_DIR = BASE / "plots"

# Sepolia-RPCs begrenzen eth_getLogs meist auf wenige tausend Bloecke.
CHUNK = 800

# Liegen zwischen zwei Messpunkten mehr als so viele Slots, gilt das als Pause
# zwischen zwei Simulationslaeufen und trennt die Abschnitte.
LUECKE = 25

# Ein Slot = 1 reale Minute = 15 Simulationsminuten
SIM_H_PRO_SLOT = 0.25

ACTION_NAMES = {0: "IDLE", 1: "CHARGE", 2: "DISCHARGE"}
COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]


def load_abi(name):
    with open(ABI_DIR / f"{name}.json") as f:
        return json.load(f)["abi"]


def fetch_events(w3, event, from_block, to_block):
    """Holt Events in Bloecken, damit der RPC nicht abbricht."""
    out = []
    start = from_block
    while start <= to_block:
        end = min(start + CHUNK - 1, to_block)
        try:
            out.extend(event.get_logs(from_block=start, to_block=end))
        except Exception as e:
            print(f"    Bereich {start}-{end} uebersprungen ({type(e).__name__})")
        start = end + 1
    return out


def letzter_abschnitt(slots):
    """
    Findet den letzten zusammenhaengenden Slot-Abschnitt.

    Beispiel: [10, 11, 12, 900, 901, 902] -> (900, 902).
    Damit faellt alles aus frueheren Simulationslaeufen weg.
    """
    if not slots:
        return None
    s = sorted(set(slots))
    ende = s[-1]
    start = ende
    for i in range(len(s) - 1, 0, -1):
        if s[i] - s[i - 1] > LUECKE:
            break
        start = s[i - 1]
    return start, ende


def sim_stunden_achse(ax, lo, hi, peak_slot):
    """
    Beschriftet die X-Achse mit Simulationsstunden statt Slot-Nummern.

    Ein Slot ist eine reale Minute und entspricht 15 Simulationsminuten,
    also 0.25 Simulationsstunden. Als Anker dient der Slot mit der
    hoechsten Gesamtproduktion - das ist der Sonnenhoechststand und damit
    etwa 12 Uhr Simulationszeit.

    Die Beschriftung zeigt die Tageszeit, senkrechte Linien markieren
    Mitternacht. Damit ist auf einen Blick erkennbar, dass die Speicher
    morgens laden und abends entladen.
    """
    def slot_zu_h(slot):
        return (slot - peak_slot) * SIM_H_PRO_SLOT + 12.0

    h_lo, h_hi = slot_zu_h(lo), slot_zu_h(hi)

    # Ticks alle 6 Simulationsstunden
    erster = (int(h_lo) // 6) * 6
    ticks, labels = [], []
    h = erster
    while h <= h_hi + 6:
        if h_lo <= h <= h_hi:
            ticks.append(peak_slot + (h - 12.0) / SIM_H_PRO_SLOT)
            labels.append(f"{int(h) % 24:02d}:00")
        h += 6
    if ticks:
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels)

    # Mitternacht markieren
    h = (int(h_lo) // 24) * 24
    while h <= h_hi + 24:
        if h_lo <= h <= h_hi:
            ax.axvline(peak_slot + (h - 12.0) / SIM_H_PRO_SLOT,
                       color="grey", linestyle="--", linewidth=0.7, alpha=0.6)
        h += 24


def main():
    span = 20000
    filtern = True
    for arg in sys.argv[1:]:
        if arg == "--alles":
            filtern = False
        else:
            span = int(arg)

    with open(CONFIG_PATH) as f:
        config = json.load(f)
    bc = config["blockchain"]

    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    latest = w3.eth.block_number
    from_block = max(0, latest - span)
    print(f"Lese Events aus Bloecken {from_block} bis {latest}\n")

    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(bc["oracle_storage_address"]),
        abi=load_abi("OracleStorage"))
    market = w3.eth.contract(
        address=Web3.to_checksum_address(bc["p2p_market_address"]),
        abi=load_abi("P2PEnergyMarket"))

    battery = None
    if "REPLACE" not in bc.get("battery_manager_address", "REPLACE"):
        battery = w3.eth.contract(
            address=Web3.to_checksum_address(bc["battery_manager_address"]),
            abi=load_abi("BatteryManager"))

    # Nur Adressen aus config.json. Alles andere (z.B. eine versehentlich im
    # Oracle registrierte Trigger-Adresse) wird verworfen.
    names = {Web3.to_checksum_address(h["address"]): h["id"]
             for h in config["households"]}

    # ── Events einsammeln ─────────────────────────────────────────
    print("  BatteryUpdated ...")
    soc_roh = defaultdict(list)
    for ev in fetch_events(w3, oracle.events.BatteryUpdated, from_block, latest):
        a = ev["args"]
        if a["household"] not in names:
            continue
        soc_roh[names[a["household"]]].append((a["slot"], a["soc"]))

    print("  MeterUpdated ...")
    meter_roh = defaultdict(list)
    for ev in fetch_events(w3, oracle.events.MeterUpdated, from_block, latest):
        a = ev["args"]
        if a["household"] not in names:
            continue
        meter_roh[names[a["household"]]].append(
            (a["slot"], int(a["production"]) - int(a["consumption"])))

    print("  SlotSettled ...")
    traded_roh = [(ev["args"]["slot"], ev["args"]["totalEnergyTraded"])
                  for ev in fetch_events(w3, market.events.SlotSettled,
                                         from_block, latest)]

    entsch_roh = defaultdict(list)
    if battery is not None:
        print("  DecisionMade ...")
        for ev in fetch_events(w3, battery.events.DecisionMade,
                               from_block, latest):
            a = ev["args"]
            if a["household"] not in names:
                continue
            entsch_roh[names[a["household"]]].append(
                (a["slot"], a["action"], a["amountWh"]))

    if not soc_roh:
        print("\nKeine BatteryUpdated-Events fuer die konfigurierten Haushalte.")
        print("Groesseren Bereich probieren: python visualize_battery.py 60000")
        return

    # ── Auf den letzten zusammenhaengenden Abschnitt einschraenken ─
    alle_slots = [s for p in soc_roh.values() for s, _ in p]
    if filtern:
        lo, hi = letzter_abschnitt(alle_slots)
        print(f"\nZeige Slots {lo} bis {hi} "
              f"(letzter zusammenhaengender Lauf, {hi - lo + 1} Slots).")
        print("Mit --alles wird der gesamte Blockbereich gezeigt.")
    else:
        lo, hi = min(alle_slots), max(alle_slots)
        print(f"\nZeige Slots {lo} bis {hi} (ungefiltert).")

    def clip(punkte):
        return sorted(p for p in punkte if lo <= p[0] <= hi)

    soc = {n: clip(p) for n, p in soc_roh.items() if clip(p)}
    meter = {n: clip(p) for n, p in meter_roh.items() if clip(p)}
    traded = clip(traded_roh)
    entsch = {n: sorted(x for x in p if lo <= x[0] <= hi)
              for n, p in entsch_roh.items()}

    # ── Kurzauswertung (CLI-Output als eigenstaendiger Nachweis) ──
    print("\n=== Zusammenfassung ===")
    for n in sorted(soc):
        werte = [v for _, v in soc[n]]
        print(f"  {n}: {len(werte)} Messpunkte, SoC {min(werte)}% bis {max(werte)}%")
    for n in sorted(entsch):
        zaehler, menge = defaultdict(int), defaultdict(int)
        for _, action, amount in entsch[n]:
            k = ACTION_NAMES.get(action, action)
            zaehler[k] += 1
            menge[k] += amount
        teile = [f"{k} {v}x ({menge[k]} Wh)" for k, v in sorted(zaehler.items())]
        print(f"  {n}: {', '.join(teile)}")
    gesamt = sum(w for _, w in traded)
    print(f"  Gehandelt: {gesamt} Wh ueber {len(traded)} Slots")

    # Anker fuer die Zeitachse: Slot mit der hoechsten Gesamtproduktion
    prod_pro_slot = defaultdict(int)
    for punkte in meter.values():
        for slot, netto in punkte:
            if netto > 0:
                prod_pro_slot[slot] += netto
    peak_slot = max(prod_pro_slot, key=prod_pro_slot.get) if prod_pro_slot else lo

    # ── Plot ──────────────────────────────────────────────────────
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(13, 9), sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 1.5]})

    # Panel 1: SoC mit Lade-/Entladepunkten
    for i, n in enumerate(sorted(soc)):
        farbe = COLORS[i % len(COLORS)]
        punkte = soc[n]
        ax1.plot([s for s, _ in punkte], [v for _, v in punkte],
                 label=n, color=farbe, linewidth=1.8)

        lookup = dict(punkte)
        lade = [(s, lookup[s]) for s, a, _ in entsch.get(n, [])
                if a == 1 and s in lookup]
        entlade = [(s, lookup[s]) for s, a, _ in entsch.get(n, [])
                   if a == 2 and s in lookup]
        if lade:
            ax1.scatter([s for s, _ in lade], [v for _, v in lade],
                        marker="^", s=40, color=farbe, edgecolors="black",
                        linewidths=0.4, zorder=5)
        if entlade:
            ax1.scatter([s for s, _ in entlade], [v for _, v in entlade],
                        marker="v", s=40, color=farbe, edgecolors="black",
                        linewidths=0.4, zorder=5)

    ax1.set_ylabel("Ladestand (%)")
    ax1.set_ylim(-5, 105)
    ax1.set_xlim(lo - 1, hi + 1)
    ax1.set_title("Batterie-Ladestand ueber Zeit  "
                  "(Dreieck hoch = CHARGE, Dreieck runter = DISCHARGE)")
    ax1.grid(alpha=0.3)
    ax1.legend(loc="upper left", ncol=max(1, len(soc)), fontsize=9)
    ax1.axhline(90, color="grey", linestyle=":", linewidth=1)
    ax1.axhline(20, color="grey", linestyle=":", linewidth=1)
    ax1.text(lo, 91.5, "MAX_SOC 90%", fontsize=7, color="grey")
    ax1.text(lo, 21.5, "MIN_SOC 20%", fontsize=7, color="grey")

    # Panel 2: Netto-Energie
    for i, n in enumerate(sorted(meter)):
        punkte = meter[n]
        ax2.plot([s for s, _ in punkte], [v for _, v in punkte],
                 label=n, color=COLORS[i % len(COLORS)], linewidth=1.4)
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_ylabel("Netto (Wh)")
    ax2.set_title("Produktion minus Verbrauch  "
                  "(ueber 0 = Produzent, unter 0 = Konsument)")
    ax2.grid(alpha=0.3)

    # Panel 3: gehandelte Energie
    if gesamt > 0:
        ax3.bar([s for s, _ in traded], [w for _, w in traded],
                color="#8c564b", width=0.9)
    else:
        # Ein leeres Panel ohne Erklaerung wirkt wie ein Fehler.
        ax3.text(0.5, 0.5,
                 "In diesem Zeitraum wurde nichts gehandelt: alle Haushalte waren\n"
                 "gleichzeitig Produzent bzw. Konsument, Ueberschuesse gingen in\n"
                 "die Batterie statt in den Markt.",
                 transform=ax3.transAxes, ha="center", va="center",
                 fontsize=9, color="#555555")
        ax3.set_ylim(0, 1)
        ax3.set_yticks([])
    ax3.set_ylabel("Gehandelt (Wh)")
    ax3.set_xlabel("Simulationszeit (gestrichelt = Mitternacht)")
    ax3.set_title("Im Markt gehandelte Energie pro Slot")
    ax3.grid(alpha=0.3, axis="y")

    for ax in (ax1, ax2, ax3):
        sim_stunden_achse(ax, lo, hi, peak_slot)

    fig.tight_layout()

    PLOT_DIR.mkdir(exist_ok=True)
    png = PLOT_DIR / "battery_soc.png"
    fig.savefig(png, dpi=150)
    print(f"\nGrafik gespeichert: {png}")

    # ── Rohdaten fuer die HTML-Visualisierung ─────────────────────
    export = {
        "meta": {
            "from_block": from_block,
            "to_block": latest,
            "slot_von": lo,
            "slot_bis": hi,
            "gefiltert": filtern,
            "oracle": bc["oracle_storage_address"],
            "market": bc["p2p_market_address"],
            "battery_manager": bc.get("battery_manager_address"),
            "max_soc": 90,
            "min_soc": 20,
            "min_soc_bad_weather": 40,
        },
        "soc": {n: [{"slot": s, "soc": v} for s, v in p] for n, p in soc.items()},
        "netto_wh": {n: [{"slot": s, "netto": v} for s, v in p]
                     for n, p in meter.items()},
        "decisions": {n: [{"slot": s, "action": ACTION_NAMES.get(a, a),
                           "amountWh": m} for s, a, m in p]
                      for n, p in entsch.items()},
        "traded": [{"slot": s, "energyWh": w} for s, w in traded],
    }
    js = PLOT_DIR / "chart_data.json"
    with open(js, "w") as f:
        json.dump(export, f, indent=2)
    print(f"Rohdaten fuer HTML: {js}")

    plt.show()


if __name__ == "__main__":
    main()