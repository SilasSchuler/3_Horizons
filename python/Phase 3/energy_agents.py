"""
energy_agents.py

Agentenschicht fuer Phase 3.

Jeder Haushalt bekommt einen Agenten mit einem Kalender verschiebbarer
Lasten (Waschmaschine, E-Auto, Waermepumpe). Wenn ein anderer Haushalt
Ueberschuss anbietet, prueft der Agent, ob er eine Last in dieses
Zeitfenster verschieben kann, und verhandelt darueber.

Aufbau in zwei Schichten:

  1. Diese Datei: Kalender, Regelwerk und Validierung. Vollstaendig
     deterministisch, keine LLM-Abhaengigkeit. Jede Zusage wird gegen den
     Kalender und die Energiebilanz geprueft.

  2. negotiation.py: die eigentliche Verhandlung. Kann regelbasiert oder
     ueber ein lokales LLM laufen. Was dort vereinbart wird, muss durch
     die Validierung hier - ein LLM darf nichts zusagen, was physikalisch
     nicht geht.

Warum diese Trennung: Ein Sprachmodell einigt sich gelegentlich auf
Unmoegliches - dieselbe Last zweimal starten, mehr Energie abnehmen als
angeboten, ein Fenster ignorieren. Die Regelschicht faengt das ab, bevor
etwas on-chain landet.
"""

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
#  Verschiebbare Lasten
# ─────────────────────────────────────────────────────────────────────

@dataclass
class FlexibleLoad:
    """
    Eine Last, deren Startzeitpunkt innerhalb eines Fensters frei waehlbar
    ist. Das ist die Flexibilitaet, mit der ein Agent handeln kann.

    fenster_von / fenster_bis in Simulationsstunden. Ein Fenster, das ueber
    Mitternacht laeuft (z.B. E-Auto 18-6 Uhr), wird korrekt behandelt.
    """
    name: str
    energie_wh: int
    fenster_von: float
    fenster_bis: float
    dauer_h: float = 1.0

    # Optionale harte Frist: Bis zu dieser Stunde MUSS die Last gelaufen
    # sein. Das E-Auto muss um 6 Uhr geladen sein, egal was es kostet -
    # anders als die Waschmaschine, die auch morgen laufen kann.
    spaetestens: Optional[float] = None

    # Wird gesetzt, sobald die Last eingeplant ist. Verhindert, dass
    # dieselbe Last mehrfach zugesagt wird.
    geplant_fuer: Optional[float] = None

    def im_fenster(self, stunde: float) -> bool:
        """Liegt die Stunde im erlaubten Startfenster?"""
        if self.fenster_von <= self.fenster_bis:
            return self.fenster_von <= stunde <= self.fenster_bis
        # Fenster ueber Mitternacht, z.B. 18 bis 6
        return stunde >= self.fenster_von or stunde <= self.fenster_bis

    def verfuegbar(self) -> bool:
        return self.geplant_fuer is None

    def stunden_bis_frist(self, jetzt: float) -> Optional[float]:
        """
        Wie viele Stunden bleiben bis zur Frist? None ohne Frist.

        Behandelt den Fall ueber Mitternacht: Ist es 22 Uhr und die Frist
        6 Uhr, bleiben acht Stunden, nicht minus sechzehn.
        """
        if self.spaetestens is None:
            return None
        rest = self.spaetestens - jetzt
        if rest < 0:
            rest += 24
        return rest

    def dringend(self, jetzt: float, schwelle: float = 4.0) -> bool:
        """
        Wird die Zeit knapp?

        Ein Agent mit dringender Last nimmt auch ein schlechteres Angebot,
        weil ihn sonst der volle Netzpreis trifft - oder das Auto morgens
        nicht faehrt.
        """
        rest = self.stunden_bis_frist(jetzt)
        return rest is not None and rest <= schwelle

    def beschreibung(self, jetzt: float = None) -> str:
        f = f"{self.fenster_von:g}-{self.fenster_bis:g} Uhr"
        status = "offen" if self.verfuegbar() else f"geplant fuer {self.geplant_fuer:g} Uhr"
        text = f"{self.name} ({self.energie_wh} Wh, Fenster {f}, {status}"
        if self.spaetestens is not None:
            text += f", muss bis {self.spaetestens:g} Uhr laufen"
            if jetzt is not None and self.dringend(jetzt):
                rest = self.stunden_bis_frist(jetzt)
                text += f" - nur noch {rest:g} h"
        return text + ")"


# ─────────────────────────────────────────────────────────────────────
#  Angebot
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Angebot:
    """Ein Haushalt bietet Ueberschuss zu einem bestimmten Zeitpunkt an."""
    verkaeufer: str
    stunde: float
    menge_wh: int
    preis_pro_kwh: float

    def beschreibung(self) -> str:
        return (f"{self.verkaeufer} bietet {self.menge_wh} Wh um "
                f"{self.stunde:g} Uhr zu {self.preis_pro_kwh:.3f} CHFD/kWh")


@dataclass
class Zusage:
    """
    Was ein Agent auf ein Angebot hin zusagt. Kommt entweder aus dem
    Regelwerk oder aus der LLM-Verhandlung - in beiden Faellen laeuft es
    durch pruefe_zusage().
    """
    kaeufer: str
    last_name: Optional[str]
    menge_wh: int
    begruendung: str = ""


# ─────────────────────────────────────────────────────────────────────
#  Agent
# ─────────────────────────────────────────────────────────────────────

class HouseholdAgent:
    """
    Vertritt einen Haushalt in der Verhandlung.

    Ein Agent ohne flexible Lasten (z.B. ein reiner Verbraucher ohne
    steuerbare Geraete) ist zulaessig - er nimmt am Markt teil, kann aber
    nichts verschieben. Genau das braucht ihr als Kontrollgruppe.
    """

    def __init__(self, haushalt_id: str, adresse: str,
                 lasten: list = None, markt_preis: float = 0.10):
        self.id = haushalt_id
        self.adresse = adresse
        self.lasten = lasten or []
        self.markt_preis = markt_preis
        self.protokoll = []       # Verhandlungsverlauf fuer die Aufzeichnung

    # ── Kalender ──────────────────────────────────────────────────

    def passende_lasten(self, stunde: float, max_wh: int) -> list:
        """
        Welche Lasten koennten in dieses Fenster verschoben werden?

        Drei Bedingungen: noch nicht eingeplant, Stunde liegt im Fenster,
        und die Last passt in die angebotene Menge.
        """
        return [l for l in self.lasten
                if l.verfuegbar()
                and l.im_fenster(stunde)
                and l.energie_wh <= max_wh]

    def hat_flexibilitaet(self) -> bool:
        return any(l.verfuegbar() for l in self.lasten)

    def situation(self) -> str:
        """Textbeschreibung fuer den LLM-Prompt."""
        if not self.lasten:
            return f"{self.id}: keine steuerbaren Geraete"
        zeilen = [f"  - {l.beschreibung()}" for l in self.lasten]
        return f"{self.id} kann verschieben:\n" + "\n".join(zeilen)

    # ── Regelbasierte Entscheidung ────────────────────────────────

    def entscheide_regelbasiert(self, angebot: Angebot) -> Optional[Zusage]:
        """
        Deterministische Referenzentscheidung.

        Nimmt die groesste Last, die ins Fenster und ins Angebot passt -
        so wird moeglichst viel guenstige Energie genutzt. Lehnt ab, wenn
        das Angebot nicht guenstiger ist als der Markt.

        Diese Funktion ist auch der Rueckfall, wenn das LLM keine
        brauchbare Antwort liefert.
        """
        kandidaten = self.passende_lasten(angebot.stunde, angebot.menge_wh)
        if not kandidaten:
            return None

        # Dringende Lasten zuerst: Wer eine Frist hat, muss sie einhalten,
        # auch wenn das Angebot nicht besonders guenstig ist.
        dringende = [l for l in kandidaten if l.dringend(angebot.stunde)]

        if dringende:
            beste = max(dringende, key=lambda l: l.energie_wh)
            rest = beste.stunden_bis_frist(angebot.stunde)
            return Zusage(
                kaeufer=self.id,
                last_name=beste.name,
                menge_wh=beste.energie_wh,
                begruendung=(f"{beste.name} muss bis {beste.spaetestens:g} Uhr laufen, "
                             f"nur noch {rest:g} h Zeit"),
            )

        # Ohne Frist lohnt sich das Verschieben nur bei echtem Preisvorteil.
        if angebot.preis_pro_kwh >= self.markt_preis:
            return None

        beste = max(kandidaten, key=lambda l: l.energie_wh)
        ersparnis = (self.markt_preis - angebot.preis_pro_kwh) * beste.energie_wh / 1000
        return Zusage(
            kaeufer=self.id,
            last_name=beste.name,
            menge_wh=beste.energie_wh,
            begruendung=(f"{beste.name} passt ins Fenster und spart "
                         f"{ersparnis:.4f} CHFD"),
        )

    # ── Validierung ───────────────────────────────────────────────

    def pruefe_zusage(self, zusage: Zusage, angebot: Angebot):
        """
        Prueft eine Zusage gegen Kalender und Energiebilanz.

        Gibt (True, last) zurueck, wenn die Zusage gueltig ist, sonst
        (False, Grund als Text). Jede Zusage - egal ob vom Regelwerk oder
        vom LLM - muss hier durch.
        """
        if zusage.last_name is None:
            return False, "keine Last benannt"

        last = next((l for l in self.lasten if l.name == zusage.last_name), None)
        if last is None:
            return False, f"Last '{zusage.last_name}' existiert nicht"

        if not last.verfuegbar():
            return False, (f"{last.name} ist bereits fuer "
                           f"{last.geplant_fuer:g} Uhr eingeplant")

        if not last.im_fenster(angebot.stunde):
            return False, (f"{last.name} darf nur zwischen "
                           f"{last.fenster_von:g} und {last.fenster_bis:g} Uhr laufen, "
                           f"angeboten war {angebot.stunde:g} Uhr")

        if zusage.menge_wh > angebot.menge_wh:
            return False, (f"{zusage.menge_wh} Wh zugesagt, aber nur "
                           f"{angebot.menge_wh} Wh im Angebot")

        if zusage.menge_wh != last.energie_wh:
            return False, (f"{last.name} braucht {last.energie_wh} Wh, "
                           f"zugesagt wurden {zusage.menge_wh} Wh")

        return True, last

    def uebernehme(self, zusage: Zusage, angebot: Angebot) -> bool:
        """Traegt eine gepruefte Zusage in den Kalender ein."""
        ok, ergebnis = self.pruefe_zusage(zusage, angebot)
        if not ok:
            self.protokoll.append(f"[abgelehnt] {ergebnis}")
            return False
        ergebnis.geplant_fuer = angebot.stunde
        self.protokoll.append(
            f"[zugesagt] {ergebnis.name} auf {angebot.stunde:g} Uhr verschoben, "
            f"{ergebnis.energie_wh} Wh von {angebot.verkaeufer}")
        return True

    def notladung(self, stunde: float):
        """
        Lasten, die jetzt laufen MUESSEN, auch ohne guenstiges Angebot.

        Ein E-Auto, das um 6 Uhr abfahren soll, wird notfalls zum vollen
        Netzpreis geladen. Der Agent hat dann zwar nichts gespart, aber
        seine Prognose stimmt trotzdem - und genau das zaehlt fuer die
        Reputation.
        """
        faellige = []
        for l in self.lasten:
            if not l.verfuegbar() or l.spaetestens is None:
                continue
            rest = l.stunden_bis_frist(stunde)
            # Letzte Gelegenheit: weniger Zeit uebrig als die Last dauert
            if rest is not None and rest <= l.dauer_h:
                l.geplant_fuer = stunde
                faellige.append(l)
                self.protokoll.append(
                    f"[notladung] {l.name} zum Marktpreis, Frist "
                    f"{l.spaetestens:g} Uhr ruecht naeher")
        return faellige

    # ── Prognose ──────────────────────────────────────────────────

    def prognose_verbrauch(self, grundlast_wh: int, stunde: float) -> int:
        """
        Prognostizierter Gesamtverbrauch = Grundlast aus dem Simulator plus
        die flexiblen Lasten, die fuer diese Stunde eingeplant sind.

        Das ist der Kern des Incentive-Modells: Ein Agent, der seine
        Flexibilitaet selbst steuert, weiss vorher, was er verbrauchen wird.
        Ohne Agent bleibt nur die Grundlast als Schaetzung, und jede
        tatsaechlich laufende Maschine erzeugt eine Abweichung.
        """
        flex = sum(l.energie_wh for l in self.lasten
                   if l.geplant_fuer is not None
                   and abs(l.geplant_fuer - stunde) < 0.5)
        return grundlast_wh + flex


# ─────────────────────────────────────────────────────────────────────
#  Standardkonfiguration
# ─────────────────────────────────────────────────────────────────────

def standard_lasten(rolle: str) -> list:
    """
    Typische Geraeteausstattung je Rolle. Die Zeitfenster sind so gewaehlt,
    dass sie sich teilweise ueberschneiden - sonst gaebe es nichts zu
    verhandeln.
    """
    if rolle == "flexibel":
        return [
            FlexibleLoad("waschmaschine", 1200, 8, 20, dauer_h=1.0),
            # Taegliche Nachladung fuer den Arbeitsweg: rund 17 km hin und
            # zurueck, bei 0.17 kWh/km also knapp 3 kWh. Das Auto steht ab
            # 18 Uhr an der Wallbox und muss um 6 Uhr abfahrbereit sein.
            # Zwei Stunden Ladezeit, letzte Gelegenheit also 4 Uhr.
            FlexibleLoad("eauto", 2800, 18, 6, dauer_h=2.0, spaetestens=6.0),
            FlexibleLoad("waermepumpe", 2000, 6, 22, dauer_h=2.0),
        ]
    if rolle == "teilflexibel":
        return [
            FlexibleLoad("waschmaschine", 1200, 8, 20, dauer_h=1.0),
            FlexibleLoad("geschirrspueler", 900, 10, 23, dauer_h=1.5),
        ]
    if rolle == "produzent":
        return [
            FlexibleLoad("waermepumpe", 2000, 6, 22, dauer_h=2.0),
        ]
    # "starr": keine steuerbaren Geraete - die Kontrollgruppe
    return []
