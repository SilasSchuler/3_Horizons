"""
build_dashboard.py

Erzeugt aus dem Verhandlungsprotokoll und den On-Chain-Daten eine
eigenstaendige HTML-Seite fuer die Praesentation.

Warum: Ein Terminal-Log ist auf dem Beamer kaum lesbar. Die Seite zeigt
dieselben Informationen als Dialog, Zeitachse und Diagramm - ohne externe
Bibliotheken, ohne Internetverbindung, eine einzige Datei zum Oeffnen.

Aufruf:
    python build_dashboard.py

Liest:
    protokolle/verhandlung_*.txt   (das neueste)
    ../config.json                 (Adressen)
    kalender/*.ics                 (Termine)
    Chain: ScoreUpdated-Events des IncentiveControllers

Schreibt:
    dashboard.html
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HIER = Path(__file__).parent
sys.path.insert(0, str(HIER))
from calendar_loader import lade_ics, tagesmuster   # noqa: E402
from run_agents import ROLLEN                       # noqa: E402

CONFIG_PATH = HIER.parent / "config.json"
ABI_DIR = HIER.parent / "abi"
PROTOKOLL_DIR = HIER / "protokolle"
KALENDER_DIR = HIER / "kalender"
AUSGABE = HIER / "dashboard.html"

CHUNK = 800

FARBEN = {"house_01": "#2563eb", "house_02": "#ea580c",
          "house_03": "#16a34a", "house_04": "#9333ea",
          "house_05": "#dc2626", "house_06": "#0891b2"}


# ─────────────────────────────────────────────────────────────────────
#  Daten sammeln
# ─────────────────────────────────────────────────────────────────────

def lies_protokoll():
    """Neuestes Verhandlungsprotokoll einlesen und in Runden zerlegen."""
    if not PROTOKOLL_DIR.exists():
        return []
    dateien = sorted(PROTOKOLL_DIR.glob("verhandlung_*.txt"))
    if not dateien:
        return []

    text = dateien[-1].read_text(encoding="utf-8", errors="replace")
    runden, aktuell = [], None

    def abschliessen():
        nonlocal aktuell
        if aktuell:
            runden.append(aktuell)
            aktuell = None

    for zeile in text.splitlines():
        z = zeile.strip()
        if not z:
            continue

        # Eine Zyklusgrenze beendet die laufende Runde. Ohne das wuerde
        # eine Notladung aus dem naechsten Zyklus faelschlich der vorigen
        # Verhandlungsrunde zugeschlagen.
        if z.startswith("### Zyklus"):
            abschliessen()
            continue

        if " bietet " in z and " Uhr zu " in z:
            abschliessen()
            aktuell = {"angebot": z, "antworten": [], "zuschlag": None,
                       "art": "angebot"}
            continue

        # Notladung ohne vorangehendes Angebot: eigener Eintrag, denn hier
        # gab es keine Verhandlung, sondern eine Zwangsentscheidung.
        if aktuell is None and "laedt" in z and "vollen Preis" in z:
            aktuell = {"angebot": None, "antworten": [z], "zuschlag": None,
                       "art": "notladung"}
            continue

        if aktuell is not None:
            if z.startswith("->"):
                aktuell["zuschlag"] = z[2:].strip()
            else:
                aktuell["antworten"].append(z)

    abschliessen()
    return runden


def hole_scores(config):
    """ScoreUpdated-Events vom IncentiveController lesen."""
    bc = config["blockchain"]
    if "REPLACE" in bc.get("incentive_controller_address", "REPLACE"):
        return {}, {}

    w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    with open(ABI_DIR / "IncentiveController.json") as f:
        ic_abi = json.load(f)["abi"]
    with open(ABI_DIR / "P2PEnergyMarket.json") as f:
        market_abi = json.load(f)["abi"]

    ic = w3.eth.contract(
        address=Web3.to_checksum_address(bc["incentive_controller_address"]),
        abi=ic_abi)
    market = w3.eth.contract(
        address=Web3.to_checksum_address(bc["p2p_market_address"]),
        abi=market_abi)

    namen = {Web3.to_checksum_address(h["address"]): h["id"]
             for h in config["households"]}

    latest = w3.eth.block_number
    start = max(0, latest - 20000)
    verlauf = defaultdict(list)

    print(f"  Lese ScoreUpdated aus Bloecken {start} bis {latest} ...")
    b = start
    while b <= latest:
        e = min(b + CHUNK - 1, latest)
        try:
            for ev in ic.events.ScoreUpdated.get_logs(from_block=b, to_block=e):
                a = ev["args"]
                if a["household"] in namen:
                    verlauf[namen[a["household"]]].append(
                        (ev["blockNumber"], int(a["newScore"]),
                         int(a.get("deviation", 0))))
        except Exception:
            pass
        b = e + 1

    # Aktueller Stand
    stand = {}
    for adresse, name in namen.items():
        try:
            stand[name] = {
                "score": ic.functions.getReputationScore(adresse).call(),
                "multiplikator": ic.functions.getPriceMultiplier(adresse).call(),
                "preis": market.functions.calculateCostFor(adresse, 1000).call(),
            }
        except Exception:
            pass

    basis = market.functions.calculateCost(1000).call()
    for v in stand.values():
        v["basis"] = basis

    return dict(verlauf), stand


def lies_kalender(config):
    """
    Termine je Haushalt fuer einen typischen Werktag.

    Gesucht wird der erste Tag der naechsten zwei Wochen, an dem ueberhaupt
    Termine stehen - sonst zeigt die Tabelle nur leere Balken, wenn der
    Kalender zufaellig an einem Wochenende oder ausserhalb seines Zeitraums
    gelesen wird.
    """
    from datetime import timedelta

    kalender = {}
    for h in config["households"]:
        ics = KALENDER_DIR / f"{h['id']}.ics"
        if ics.exists():
            kalender[h["id"]] = lade_ics(ics)

    if not kalender:
        return {}, None

    # Ersten Tag finden, an dem mindestens ein Haushalt Termine hat
    start = date.today()
    gewaehlt = start
    for versatz in range(0, 21):
        tag = start + timedelta(days=versatz)
        if any(tagesmuster(t, tag)["termine"] for t in kalender.values()):
            gewaehlt = tag
            break

    ergebnis = {}
    for hid, termine in kalender.items():
        m = tagesmuster(termine, gewaehlt)
        ergebnis[hid] = {
            "termine": [t for _, t in m["termine"]],
            "abwesend": m["abwesend"],
            "homeoffice": m["homeoffice"],
            "weg": m["weg_stunden"],
        }
    return ergebnis, gewaehlt


# ─────────────────────────────────────────────────────────────────────
#  Diagramm als SVG
# ─────────────────────────────────────────────────────────────────────

def score_diagramm(verlauf, breite=880, hoehe=300):
    """
    Zeichnet den Score-Verlauf als SVG - ohne externe Bibliothek.

    Die X-Achse ist die Blocknummer, nicht der Messpunkt-Index. Das ist
    wichtig, weil die Haushalte unterschiedlich viele Messpunkte haben:
    Schlaegt eine Transaktion fehl, fehlt fuer diesen Haushalt ein Wert.
    Wuerde jede Kurve ueber ihren eigenen Index normiert, laegen Punkte
    uebereinander, die zeitlich weit auseinander liegen - der Vergleich
    waere wertlos.
    """
    if not verlauf:
        return "<p class='hinweis'>Noch keine Score-Daten vorhanden.</p>"

    punkte_gesamt = [p for reihe in verlauf.values() for p in reihe]
    if len(punkte_gesamt) < 2:
        return "<p class='hinweis'>Zu wenige Messpunkte fuer eine Kurve.</p>"

    block_min = min(p[0] for p in punkte_gesamt)
    block_max = max(p[0] for p in punkte_gesamt)
    if block_max == block_min:
        return "<p class='hinweis'>Alle Messpunkte im selben Block.</p>"

    rand_l, rand_r, rand_o, rand_u = 50, 20, 20, 40
    pb = breite - rand_l - rand_r
    ph = hoehe - rand_o - rand_u

    def x_von(block):
        return rand_l + (block - block_min) / (block_max - block_min) * pb

    def y_von(score):
        return rand_o + ph - (score / 1000) * ph

    teile = [f'<svg viewBox="0 0 {breite} {hoehe}" class="chart">']

    for wert in (0, 250, 500, 750, 1000):
        y = y_von(wert)
        teile.append(f'<line x1="{rand_l}" y1="{y:.0f}" x2="{breite-rand_r}" '
                     f'y2="{y:.0f}" class="grid"/>')
        teile.append(f'<text x="{rand_l-8}" y="{y+4:.0f}" class="tick" '
                     f'text-anchor="end">{wert}</text>')

    y500 = y_von(500)
    teile.append(f'<line x1="{rand_l}" y1="{y500:.0f}" x2="{breite-rand_r}" '
                 f'y2="{y500:.0f}" class="neutral"/>')
    teile.append(f'<text x="{breite-rand_r-4}" y="{y500-6:.0f}" class="tick" '
                 f'text-anchor="end">neutral</text>')

    for name, reihe in sorted(verlauf.items()):
        if len(reihe) < 2:
            continue
        farbe = FARBEN.get(name, "#666")
        # Nach Block sortieren: die Events kommen blockweise, aber nicht
        # zwingend in aufsteigender Reihenfolge zurueck.
        sortiert = sorted(reihe, key=lambda p: p[0])
        koords = [f"{x_von(b):.1f},{y_von(score):.1f}"
                  for b, score, _ in sortiert]
        teile.append(f'<polyline points="{" ".join(koords)}" fill="none" '
                     f'stroke="{farbe}" stroke-width="2.5"/>')
        lx, ly = koords[-1].split(",")
        teile.append(f'<circle cx="{lx}" cy="{ly}" r="4" fill="{farbe}"/>')

    teile.append(f'<text x="{breite/2:.0f}" y="{hoehe-8}" class="tick" '
                 f'text-anchor="middle">Zeitverlauf (Blocknummer)</text>')
    teile.append("</svg>")
    return "".join(teile)


# ─────────────────────────────────────────────────────────────────────
#  HTML
# ─────────────────────────────────────────────────────────────────────

def baue_html(runden, verlauf, stand, kalender, config, kal_tag=None):
    bc = config["blockchain"]

    # ── Verhandlung als Dialog ────────────────────────────────────
    # Nur die Runden zeigen, in denen tatsaechlich etwas zustande kam.
    # Reihenweise Absagen sind fuer die Praesentation uninteressant - sie
    # entstehen, wenn der Ueberschuss zu klein fuer jedes Geraet ist.
    def zeigenswert(r):
        # Eine Notladung ist immer erwaehnenswert: Sie zeigt, was passiert,
        # wenn niemand etwas anbietet und die Frist trotzdem laeuft.
        if r.get("art") == "notladung":
            return True
        return bool(r["zuschlag"]) and "niemand" not in r["zuschlag"].lower()

    interessant = [r for r in runden if zeigenswert(r)]
    if len(interessant) < 2:
        # Nichts zustande gekommen: dann wenigstens ein paar Runden zeigen,
        # damit der Abschnitt nicht leer bleibt.
        interessant = runden[-3:]

    dialog = []
    for r in interessant[-6:]:
        m = (re.match(r"(\S+) bietet (\d+) Wh um ([\d.]+) Uhr zu ([\d.]+)",
                      r["angebot"]) if r["angebot"] else None)
        if m:
            wer, menge, stunde, preis = m.groups()
            farbe = FARBEN.get(wer, "#666")
            dialog.append(
                f'<div class="runde">'
                f'<div class="angebot" style="border-color:{farbe}">'
                f'<span class="wer" style="color:{farbe}">{wer}</span>'
                f'<span class="text">Ich habe <b>{menge} Wh</b> uebrig um '
                f'{float(stunde):g} Uhr. Ich gebe sie fuer {preis} CHFD/kWh ab '
                f'statt der ueblichen 0.100.</span></div>')
        elif r.get("art") == "notladung":
            # Kein Angebot - der Agent handelt aus eigenem Antrieb.
            dialog.append('<div class="runde ohne-angebot">')
        else:
            dialog.append(f'<div class="runde"><div class="angebot">'
                          f'{r["angebot"]}</div>')

        for a in r["antworten"]:
            ma = re.match(r"(\S+): (.*)", a)
            if not ma:
                continue
            wer, was = ma.groups()
            farbe = FARBEN.get(wer, "#666")
            if "laedt" in was and "vollen Preis" in was:
                dialog.append(
                    f'<div class="antwort notladung">'
                    f'<span class="wer" style="color:{farbe}">{wer}</span>'
                    f'<span class="text">Kein Angebot mehr da. Ich lade jetzt '
                    f'zum vollen Netzpreis &ndash; die Frist laesst mir keine '
                    f'Wahl mehr.</span></div>')
            elif "lehnt ab" in was or "nichts Passendes" in was:
                dialog.append(
                    f'<div class="antwort ablehnung">'
                    f'<span class="wer" style="color:{farbe}">{wer}</span>'
                    f'<span class="text">Danke, aber ich kann gerade nichts '
                    f'verschieben.</span></div>')
            elif "verworfen" in was:
                dialog.append(
                    f'<div class="antwort verworfen">'
                    f'<span class="wer" style="color:{farbe}">{wer}</span>'
                    f'<span class="text">Zusage von der Pruefung abgelehnt: '
                    f'{was.split("(")[-1].rstrip(")")}</span></div>')
            else:
                mb = re.match(r"bietet fuer (\S+) \((\d+) Wh\) - (.*)", was)
                if mb:
                    last, menge, grund = mb.groups()
                    eilig = "muss bis" in grund
                    cls = "zusage frist" if eilig else "zusage"
                    einleitung = ("Ich nehme sie &ndash; ich habe keine Wahl mehr."
                                  if eilig else "Ich nehme sie.")
                    dialog.append(
                        f'<div class="antwort {cls}">'
                        f'<span class="wer" style="color:{farbe}">{wer}</span>'
                        f'<span class="text">{einleitung} Ich lege '
                        f'meine <b>{last}</b> ({menge} Wh) in dieses Fenster.'
                        f'<br><span class="grund">{grund}</span></span></div>')
                else:
                    dialog.append(f'<div class="antwort"><span class="wer">'
                                  f'{wer}</span><span class="text">{was}'
                                  f'</span></div>')

        if r["zuschlag"] and "niemand" not in r["zuschlag"].lower():
            dialog.append(f'<div class="zuschlag">{r["zuschlag"]}</div>')
        elif r["zuschlag"]:
            dialog.append('<div class="keiner">Das Angebot war zu klein fuer '
                          'jedes Geraet &ndash; niemand konnte es brauchen.</div>')
        dialog.append("</div>")

    if not dialog:
        dialog = ["<p class='hinweis'>Noch kein Verhandlungsprotokoll "
                  "vorhanden. Zuerst run_agents.py ausfuehren.</p>"]

    # ── Kalender ──────────────────────────────────────────────────
    kal_zeilen = []
    for hid, k in sorted(kalender.items()):
        farbe = FARBEN.get(hid, "#666")
        if k["abwesend"]:
            zustand = "ganztags abwesend"
            wirkung = "keine Geraete steuerbar"
        elif k["homeoffice"]:
            zustand = "Homeoffice"
            wirkung = "volle Flexibilitaet den ganzen Tag"
        elif k["weg"]:
            # Zusammenhaengende Bloecke bilden, statt nur erste bis letzte
            # Stunde zu nehmen - sonst verschwinden Luecken zwischen zwei
            # Terminen, in denen jemand durchaus zuhause ist.
            stunden = sorted(k["weg"])
            bloecke, start, vorher = [], stunden[0], stunden[0]
            for h in stunden[1:]:
                if h == vorher + 1:
                    vorher = h
                else:
                    bloecke.append((start, vorher + 1))
                    start, vorher = h, h
            bloecke.append((start, vorher + 1))

            zustand = "ausser Haus " + ", ".join(
                f"{v}-{b} Uhr" for v, b in bloecke)
            frei = 24 - len(stunden)
            if len(bloecke) == 1:
                wirkung = f"Geraete erst ab {bloecke[0][1]} Uhr startbar"
            else:
                wirkung = f"{len(bloecke)} Zeitfenster, {frei} h zuhause"
        else:
            zustand = "keine Termine"
            wirkung = "Standardfenster"

        balken = []
        for h in range(24):
            if k["abwesend"]:
                cls = "weg"
            elif h in k["weg"]:
                cls = "weg"
            else:
                cls = "da"
            balken.append(f'<div class="std {cls}" title="{h}:00"></div>')

        kal_zeilen.append(
            f'<tr><td class="hid" style="color:{farbe}">{hid}</td>'
            f'<td>{zustand}</td>'
            f'<td class="tag">{"".join(balken)}</td>'
            f'<td class="wirkung">{wirkung}</td></tr>')

    # ── Ergebnistabelle ───────────────────────────────────────────
    erg_zeilen = []
    for hid in sorted(stand):
        s = stand[hid]
        farbe = FARBEN.get(hid, "#666")
        agent = ROLLEN.get(hid) is not None
        diff = (s["preis"] - s["basis"]) / s["basis"] * 100
        klasse = "guenstig" if diff < -1 else ("teuer" if diff > 1 else "")
        vorzeichen = "+" if diff > 0 else ""
        erg_zeilen.append(
            f'<tr><td class="hid" style="color:{farbe}">{hid}</td>'
            f'<td>{"ja" if agent else "nein"}</td>'
            f'<td>{s["score"]}</td>'
            f'<td>{s["multiplikator"]}</td>'
            f'<td class="{klasse}">{vorzeichen}{diff:.1f} %</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Energiegemeinschaft mit verhandelnden Agenten</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
         margin: 0; padding: 32px; background: #f6f7f9; color: #1a1a1a;
         line-height: 1.5; }}
  .seite {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; }}
  .unter {{ color: #666; margin-bottom: 28px; font-size: 14px; }}
  h2 {{ font-size: 18px; margin: 36px 0 4px; }}
  .erklaerung {{ color: #555; font-size: 14px; margin-bottom: 16px; }}
  .karte {{ background: #fff; border: 1px solid #e2e5e9; border-radius: 10px;
            padding: 20px; margin-bottom: 8px; }}

  .runde {{ border-left: 3px solid #e2e5e9; padding-left: 16px;
            margin-bottom: 22px; }}
  .angebot {{ background: #eef4ff; border-left: 4px solid #2563eb;
              border-radius: 0 8px 8px 0; padding: 12px 14px;
              margin-bottom: 8px; }}
  .antwort {{ background: #f8f9fa; border-radius: 8px; padding: 10px 14px;
              margin: 6px 0 6px 28px; font-size: 14px; }}
  .antwort.zusage {{ background: #f0fdf4; }}
  .antwort.ablehnung {{ background: #fafafa; color: #777; }}
  .antwort.verworfen {{ background: #fef2f2; color: #991b1b; }}
  .antwort.frist {{ background: #fffbeb; border-left: 3px solid #f59e0b; }}
  .antwort.notladung {{ background: #fff7ed; border-left: 3px solid #ea580c;
                        color: #7c2d12; }}
  .runde.ohne-angebot {{ border-left-color: #fdba74; }}
  .keiner {{ color: #888; font-size: 13px; margin: 6px 0 0 28px;
             font-style: italic; }}
  .wer {{ font-weight: 600; display: block; font-size: 13px;
          margin-bottom: 3px; }}
  .grund {{ color: #666; font-size: 13px; font-style: italic; }}
  .zuschlag {{ font-weight: 600; color: #15803d; margin: 8px 0 0 28px;
               font-size: 14px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ text-align: left; font-weight: 600; color: #666; font-size: 12px;
        text-transform: uppercase; letter-spacing: .04em;
        padding: 0 12px 8px 0; }}
  td {{ padding: 9px 12px 9px 0; border-top: 1px solid #eef0f2; }}
  .hid {{ font-weight: 600; }}
  .guenstig {{ color: #15803d; font-weight: 600; }}
  .teuer {{ color: #b91c1c; font-weight: 600; }}
  .wirkung {{ color: #666; font-size: 13px; }}

  .tag {{ display: flex; gap: 1px; width: 240px; }}
  .std {{ height: 18px; flex: 1; border-radius: 1px; }}
  .std.da {{ background: #bbf7d0; }}
  .std.weg {{ background: #e5e7eb; }}

  .chart {{ width: 100%; height: auto; }}
  .grid {{ stroke: #eef0f2; stroke-width: 1; }}
  .neutral {{ stroke: #cbd5e1; stroke-width: 1; stroke-dasharray: 4 4; }}
  .tick {{ font-size: 11px; fill: #888; }}
  .legende {{ display: flex; gap: 20px; margin-top: 10px; font-size: 13px; }}
  .legende span {{ display: flex; align-items: center; gap: 6px; }}
  .punkt {{ width: 11px; height: 3px; border-radius: 2px; }}
  .hinweis {{ color: #888; font-style: italic; }}
  .adressen {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px;
               color: #666; margin-top: 36px; line-height: 1.9; }}
</style>
</head>
<body>
<div class="seite">

  <h1>Energiegemeinschaft mit verhandelnden Agenten</h1>
  <div class="unter">Team 3_Horizons &middot; Sepolia Testnet</div>

  <h2>Die Agenten verhandeln</h2>
  <div class="erklaerung">
    Jeder Haushalt hat einen Agenten, der seine verschiebbaren Geraete kennt.
    Wer Ueberschuss hat, bietet ihn guenstiger an als den Marktpreis. Wer ein
    Geraet in dieses Zeitfenster legen kann, nimmt an. Die Entscheidungen
    formuliert ein lokal laufendes Sprachmodell; jede Zusage wird
    anschliessend gegen Kalender und Energiebilanz geprueft.
    Manche Geraete haben eine harte Frist &ndash; das E-Auto muss um sechs
    abfahrbereit sein. Rueckt sie naeher, nimmt der Agent auch ein Angebot
    ohne Ersparnis, und im Notfall laedt er zum vollen Netzpreis.
  </div>
  <div class="karte">{"".join(dialog)}</div>
  <div class="erklaerung" style="margin-top:8px">
    Gezeigt sind die Runden, in denen eine Verschiebung zustande kam.
    Ist der Ueberschuss kleiner als jedes verschiebbare Geraet, lehnen alle
    ab &ndash; das passiert morgens und abends regelmaessig.
  </div>

  <h2>Woher die Zeitfenster kommen</h2>
  <div class="erklaerung">
    Aus echten Kalenderdateien im ICS-Format, wie Outlook und Google sie
    exportieren. Wer tagsueber im Buero ist, kann mittags keine Waschmaschine
    starten &ndash; das Fenster verschiebt sich automatisch nach hinten.
  </div>
  <div class="karte">
    <table>
      <tr><th>Haushalt</th><th>Kalender am {kal_tag.strftime("%d.%m.") if kal_tag else "Stichtag"}</th><th>0 bis 24 Uhr</th>
          <th>Folge</th></tr>
      {"".join(kal_zeilen) or '<tr><td colspan="4" class="hinweis">Keine Kalender geladen.</td></tr>'}
    </table>
  </div>

  <h2>Reputation ueber die Zeit</h2>
  <div class="erklaerung">
    Wer seinen Verbrauch vorher meldet und dann trifft, baut Reputation auf.
    Der Wert ist ein gleitender Durchschnitt ueber acht Slots: Ein einzelner
    Fehltritt zaehlt wenig, dauerhafte Ungenauigkeit schon.
  </div>
  <div class="karte">
    {score_diagramm(verlauf)}
    <div class="legende">
      {"".join(f'<span><i class="punkt" style="background:{FARBEN.get(n, "#666")}"></i>{n}</span>' for n in sorted(verlauf))}
    </div>
  </div>

  <h2>Was das im Preis ausmacht</h2>
  <div class="erklaerung">
    Der Reputationswert wirkt direkt auf den Preis, den ein Haushalt beim
    naechsten Energiekauf zahlt. Der Smart Contract rechnet das bei jeder
    Abrechnung selbst aus.
  </div>
  <div class="karte">
    <table>
      <tr><th>Haushalt</th><th>Agent</th><th>Reputation</th>
          <th>Faktor</th><th>gegenueber Basispreis</th></tr>
      {"".join(erg_zeilen) or '<tr><td colspan="5" class="hinweis">Keine Daten.</td></tr>'}
    </table>
  </div>

  <div class="adressen">
    OracleStorage &nbsp; {bc['oracle_storage_address']}<br>
    P2PEnergyMarket &nbsp; {bc['p2p_market_address']}<br>
    BatteryManager &nbsp; {bc.get('battery_manager_address', '-')}<br>
    IncentiveController &nbsp; {bc.get('incentive_controller_address', '-')}
  </div>

</div>
</body>
</html>"""


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    print("Sammle Daten ...")
    runden = lies_protokoll()
    print(f"  {len(runden)} Verhandlungsrunden im Protokoll")

    kalender, kal_tag = lies_kalender(config)
    print(f"  {len(kalender)} Kalender, Stichtag {kal_tag}")

    verlauf, stand = hole_scores(config)
    print(f"  Score-Verlauf fuer {len(verlauf)} Haushalte")

    AUSGABE.write_text(
        baue_html(runden, verlauf, stand, kalender, config, kal_tag),
        encoding="utf-8")
    print(f"\nFertig: {AUSGABE}")
    print("Im Browser oeffnen - die Datei ist eigenstaendig, ohne Internet.")


if __name__ == "__main__":
    main()
