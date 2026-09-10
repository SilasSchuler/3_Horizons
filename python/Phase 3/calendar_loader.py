"""
calendar_loader.py

Liest Kalenderdateien im ICS-Format und leitet daraus ab, wann ein
Haushalt seine verschiebbaren Lasten laufen lassen kann.

ICS ist das Format, das Outlook, Google Calendar, Apple Kalender und
Thunderbird exportieren. Ihr koennt also entweder die mitgelieferten
Beispielkalender verwenden oder einen echten Kalender exportieren und
hier einlesen.

  Google Calendar:  Einstellungen -> Import & Export -> Exportieren
  Outlook:          Datei -> Speichern unter -> iCalendar-Format
  Apple Kalender:   Ablage -> Exportieren

Wie Termine auf Energie wirken:

  "Abwesend", "Ferien", "Urlaub"   -> niemand zuhause. Waschmaschine und
                                      Geschirrspueler laufen nicht, das
                                      E-Auto ist nicht an der Ladestation.
  "Homeoffice", "Home Office"      -> ganztags jemand da, flexible Lasten
                                      koennen den ganzen Tag laufen.
  "Buero", "Arbeit", "Vorlesung"   -> tagsueber weg. Lasten nur abends,
                                      das E-Auto laedt erst nach der
                                      Rueckkehr.
  alles andere                      -> keine Auswirkung

Absichtlich einfach gehalten: Der Nutzen liegt darin, dass die
Lastfenster aus einer echten, nachvollziehbaren Quelle kommen statt aus
willkuerlich gesetzten Konstanten.

Kein externes Paket noetig - der Parser deckt den Teil von RFC 5545 ab,
den Outlook und Google fuer einfache Termine schreiben.
"""

import re
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────
#  ICS lesen
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Termin:
    titel: str
    start: datetime
    ende: datetime
    ganztags: bool = False

    def deckt_stunde(self, tag: date, stunde: float) -> bool:
        """Faellt diese Uhrzeit an diesem Tag in den Termin?"""
        if self.ganztags:
            return self.start.date() <= tag < self.ende.date()
        zeitpunkt = datetime.combine(tag, datetime.min.time()) + \
            timedelta(hours=stunde)
        return self.start <= zeitpunkt < self.ende


def _parse_zeit(wert: str, params: str):
    """
    ICS-Zeitangaben kommen in mehreren Varianten:
      20260910T140000Z    UTC
      20260910T140000     lokal
      20260910            ganztags (dann steht VALUE=DATE in den Parametern)
    """
    wert = wert.strip()
    ganztags = "VALUE=DATE" in params.upper() and "T" not in wert

    if ganztags:
        return datetime.strptime(wert, "%Y%m%d"), True
    wert = wert.rstrip("Z")
    return datetime.strptime(wert, "%Y%m%dT%H%M%S"), False


def lade_ics(pfad) -> list:
    """
    Liest alle VEVENT-Bloecke aus einer ICS-Datei.

    Beruecksichtigt die Zeilenfaltung nach RFC 5545: Lange Zeilen werden
    umgebrochen und mit einem Leerzeichen oder Tab fortgesetzt. Outlook
    macht das regelmaessig bei langen Terminbezeichnungen.
    """
    roh = Path(pfad).read_text(encoding="utf-8", errors="replace")

    # Gefaltete Zeilen wieder zusammensetzen
    entfaltet = re.sub(r"\r?\n[ \t]", "", roh)

    termine = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", entfaltet, re.S):
        titel, start, ende, ganztags = None, None, None, False
        for zeile in block.splitlines():
            if ":" not in zeile:
                continue
            kopf, _, wert = zeile.partition(":")
            name, _, params = kopf.partition(";")
            name = name.strip().upper()

            if name == "SUMMARY":
                titel = wert.strip()
            elif name == "DTSTART":
                try:
                    start, ganztags = _parse_zeit(wert, params)
                except ValueError:
                    pass
            elif name == "DTEND":
                try:
                    ende, _ = _parse_zeit(wert, params)
                except ValueError:
                    pass

        if titel and start:
            if ende is None:
                ende = start + (timedelta(days=1) if ganztags
                                else timedelta(hours=1))
            termine.append(Termin(titel, start, ende, ganztags))

    return termine


# ─────────────────────────────────────────────────────────────────────
#  Termine auf Lastfenster abbilden
# ─────────────────────────────────────────────────────────────────────

ABWESEND = ("abwesend", "ferien", "urlaub", "away", "vacation", "reise")
HOMEOFFICE = ("homeoffice", "home office", "hoffice")
UNTERWEGS = ("buero", "büro", "arbeit", "vorlesung", "office", "work", "uni")


def _kategorie(titel: str):
    t = titel.lower()
    if any(w in t for w in ABWESEND):
        return "abwesend"
    if any(w in t for w in HOMEOFFICE):
        return "homeoffice"
    if any(w in t for w in UNTERWEGS):
        return "unterwegs"
    return None


def tagesmuster(termine: list, tag: date) -> dict:
    """
    Wertet den Kalender fuer einen Tag aus.

    Rueckgabe enthaelt die Kategorie und die Stunden, in denen der
    Haushalt besetzt ist. Daraus leiten sich die Lastfenster ab.
    """
    ganztags_abwesend = False
    homeoffice = False
    weg_stunden = set()
    relevante = []

    for t in termine:
        kat = _kategorie(t.titel)
        if kat is None:
            continue
        # Deckt der Termin diesen Tag ueberhaupt ab?
        if not any(t.deckt_stunde(tag, h) for h in range(24)):
            continue
        relevante.append((kat, t.titel))

        if kat == "abwesend":
            ganztags_abwesend = True
        elif kat == "homeoffice":
            homeoffice = True
        elif kat == "unterwegs":
            for h in range(24):
                if t.deckt_stunde(tag, h):
                    weg_stunden.add(h)

    return {
        "abwesend": ganztags_abwesend,
        "homeoffice": homeoffice,
        "weg_stunden": sorted(weg_stunden),
        "termine": relevante,
    }


def passe_lasten_an(lasten: list, muster: dict) -> list:
    """
    Verschiebt die Zeitfenster der Lasten anhand des Tagesmusters.

    Gibt eine neue Liste zurueck, die urspruengliche bleibt unveraendert -
    so laesst sich pro Tag neu rechnen, ohne dass sich Aenderungen
    aufsummieren.
    """
    from copy import deepcopy
    angepasst = deepcopy(lasten)

    if muster["abwesend"]:
        # Niemand da: nichts laeuft, das Auto steht woanders
        return []

    if muster["homeoffice"]:
        # Ganztags jemand zuhause, Fenster bleiben weit
        return angepasst

    weg = muster["weg_stunden"]
    if not weg:
        return angepasst

    rueckkehr = max(weg) + 1
    for last in angepasst:
        if last.name == "eauto":
            # Das Auto ist erst nach der Rueckkehr an der Ladestation
            last.fenster_von = max(last.fenster_von, rueckkehr)
        elif last.name in ("waschmaschine", "geschirrspueler"):
            # Diese Geraete startet man, wenn jemand da ist
            last.fenster_von = max(last.fenster_von, rueckkehr)
        # Waermepumpe laeuft unabhaengig von Anwesenheit weiter

    # Fenster, die dadurch leer geworden sind, entfallen
    return [l for l in angepasst
            if l.fenster_von < l.fenster_bis or l.fenster_von > l.fenster_bis]


# ─────────────────────────────────────────────────────────────────────
#  Beispielkalender erzeugen
# ─────────────────────────────────────────────────────────────────────

VORLAGEN = {
    "berufstaetig": [
        ("Buero", 8, 17, "MO,TU,WE,TH,FR"),
        ("Sport", 19, 20.5, "TU,TH"),
    ],
    "homeoffice": [
        ("Homeoffice", 8, 17, "MO,WE,FR"),
        ("Buero", 8, 17, "TU,TH"),
    ],
    "schicht": [
        ("Arbeit Spaetschicht", 14, 22, "MO,TU,WE,TH,FR"),
    ],
    "familie": [
        ("Buero", 8, 16, "MO,TU,WE,TH"),
        ("Homeoffice", 8, 16, "FR"),
    ],
    "ferien": [
        ("Ferien", None, None, None),   # ganztaegig, siehe unten
    ],
}


def schreibe_beispielkalender(pfad, vorlage: str, startdatum: date = None,
                              tage: int = 7, name: str = "Haushalt"):
    """
    Erzeugt eine ICS-Datei nach einer der Vorlagen.

    Die Datei laesst sich in Outlook oder Google Calendar importieren -
    praktisch fuer die Praesentation, weil man zeigen kann, dass es echte
    Kalenderdaten sind und keine erfundene Struktur.
    """
    startdatum = startdatum or date.today()
    wochentage = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

    zeilen = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//3_Horizons//Energy Trading Challenge//DE",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{name}",
    ]

    lfd = 0
    for i in range(tage):
        tag = startdatum + timedelta(days=i)
        kuerzel = wochentage[tag.weekday()]

        for titel, von, bis, tage_str in VORLAGEN[vorlage]:
            if von is None:
                # Ganztagstermin
                lfd += 1
                zeilen += [
                    "BEGIN:VEVENT",
                    f"UID:{vorlage}-{lfd}@3horizons.local",
                    f"DTSTART;VALUE=DATE:{tag:%Y%m%d}",
                    f"DTEND;VALUE=DATE:{tag + timedelta(days=1):%Y%m%d}",
                    f"SUMMARY:{titel}",
                    "END:VEVENT",
                ]
                continue

            if tage_str and kuerzel not in tage_str.split(","):
                continue

            lfd += 1
            sh, sm = int(von), int((von % 1) * 60)
            eh, em = int(bis), int((bis % 1) * 60)
            zeilen += [
                "BEGIN:VEVENT",
                f"UID:{vorlage}-{lfd}@3horizons.local",
                f"DTSTART:{tag:%Y%m%d}T{sh:02d}{sm:02d}00",
                f"DTEND:{tag:%Y%m%d}T{eh:02d}{em:02d}00",
                f"SUMMARY:{titel}",
                "END:VEVENT",
            ]

    zeilen.append("END:VCALENDAR")
    Path(pfad).write_text("\r\n".join(zeilen), encoding="utf-8")
    return pfad
