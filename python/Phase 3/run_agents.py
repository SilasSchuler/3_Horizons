"""
run_agents.py

Hauptskript fuer Phase 3. Verbindet Agenten, Verhandlung und Blockchain.

Ablauf pro Zyklus:

  1. Aktuelle Oracle-Daten lesen (Produktion, Verbrauch, Wetter)
  2. Kalender auswerten, Lastfenster fuer heute anpassen
  3. Agenten verhandeln ueber die Ueberschuesse
  4. Prognose je Haushalt on-chain einreichen
  5. Einen Slot warten
  6. Ist-Werte on-chain nachtragen -> Contract berechnet den Score
  7. Scores und Preismultiplikatoren ausgeben

Der Unterschied, um den es geht:

  Haushalte MIT Agent steuern ihre flexiblen Lasten selbst. Sie wissen
  vorher, was sie verbrauchen werden, und treffen ihre Prognose.

  Haushalte OHNE Agent melden nur eine grobe Schaetzung. Jede Maschine,
  die spontan laeuft, erzeugt eine Abweichung - und kostet Score.

Aufruf:
    python run_agents.py                # regelbasiert, 5 Zyklen
    python run_agents.py --llm          # mit Ollama-Verhandlung
    python run_agents.py --llm 10       # 10 Zyklen
    python run_agents.py --trocken      # ohne Blockchain, nur Verhandlung
    python run_agents.py --demo --llm   # Praesentationsszenario, sechs Haushalte

Voraussetzung: setup_phase3.py ist gelaufen, config.json enthaelt die
Adressen von Market und IncentiveController.
"""

# ==================================================================
#  LLM-Anbindung (Ollama)
# ==================================================================


import json

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e2b"
TIMEOUT = 180


class LLMNichtErreichbar(Exception):
    """Ollama laeuft nicht oder antwortet nicht rechtzeitig."""


def _extrahiere_json(text: str):
    """
    Holt das letzte vollstaendige JSON-Objekt aus einem Text.

    Reasoning-Modelle schreiben ihre Ueberlegungen vor die Antwort, teils
    mit JSON-Beispielen mittendrin. Deshalb wird von hinten gesucht: Das
    letzte geschlossene Objekt ist die eigentliche Antwort.

    Zusaetzlich werden Markdown-Codezaeune entfernt, die manche Modelle
    trotz gegenteiliger Anweisung setzen.
    """
    if not text:
        return None

    text = re.sub(r"```(?:json)?", "", text)

    # Von hinten nach einer schliessenden Klammer suchen und die passende
    # oeffnende dazu finden.
    ende = text.rfind("}")
    while ende != -1:
        tiefe = 0
        for i in range(ende, -1, -1):
            if text[i] == "}":
                tiefe += 1
            elif text[i] == "{":
                tiefe -= 1
                if tiefe == 0:
                    try:
                        return json.loads(text[i:ende + 1])
                    except json.JSONDecodeError:
                        break
        ende = text.rfind("}", 0, ende)
    return None


def frage(prompt: str, modell: str = DEFAULT_MODEL,
          schema: dict = None, versuche: int = 3):
    """
    Schickt einen Prompt an Ollama und gibt das geparste JSON zurueck.

    schema: optionale Liste von Pflichtfeldern. Fehlt eines, wird gezielt
            nachgefragt statt die Antwort zu verwerfen - das kostet einen
            weiteren Durchlauf, liefert aber meist beim zweiten Mal ein
            brauchbares Ergebnis.

    Wirft LLMNichtErreichbar, wenn Ollama nicht antwortet. Gibt None zurueck,
    wenn nach allen Versuchen kein gueltiges JSON kam - der Aufrufer faellt
    dann auf das Regelwerk zurueck.
    """
    aktueller_prompt = prompt

    for versuch in range(versuche):
        daten = json.dumps({
            "model": modell,
            "prompt": aktueller_prompt,
            "stream": False,
            # Niedrige Temperatur: Wir wollen nachvollziehbare
            # Verhandlungen, keine kreativen Ausreisser.
            "options": {"temperature": 0.3},
        }).encode()

        req = urllib.request.Request(
            OLLAMA_URL, data=daten,
            headers={"Content-Type": "application/json"})

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                antwort = json.loads(r.read())["response"]
        except urllib.error.URLError as e:
            raise LLMNichtErreichbar(
                f"Ollama nicht erreichbar ({e}). Laeuft 'ollama serve'?")
        except TimeoutError:
            raise LLMNichtErreichbar(
                f"Ollama hat nach {TIMEOUT}s nicht geantwortet.")

        ergebnis = _extrahiere_json(antwort)

        if ergebnis is None:
            aktueller_prompt = (
                prompt + "\n\nDeine letzte Antwort enthielt kein gueltiges "
                "JSON. Antworte NUR mit dem JSON-Objekt, ohne Erklaerung "
                "davor oder danach.")
            continue

        if schema:
            fehlend = [f for f in schema if f not in ergebnis]
            if fehlend:
                aktueller_prompt = (
                    prompt + f"\n\nDeine letzte Antwort war unvollstaendig, "
                    f"es fehlten: {', '.join(fehlend)}. Antworte erneut mit "
                    f"allen Feldern.")
                continue

        return ergebnis

    return None


def verfuegbar(modell: str = DEFAULT_MODEL) -> bool:
    """Prueft, ob Ollama laeuft und das Modell geladen werden kann."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags",
                                    timeout=5) as r:
            modelle = [m["name"] for m in json.loads(r.read())["models"]]
        return modell in modelle
    except Exception:
        return False


# ==================================================================
#  Verhandlung
# ==================================================================




PROMPT = """Du bist der Energie-Agent von Haus {haus}.

Deine steuerbaren Geraete:
{geraete}

Angebot von {verkaeufer}:
  {menge} Wh verfuegbar um {stunde:g} Uhr
  Preis {preis:.3f} CHFD/kWh statt {markt:.3f} vom Markt

Regeln:
- Du darfst nur ein Geraet nennen, das JETZT verfuegbar ist und dessen
  Zeitfenster {stunde:g} Uhr enthaelt.
- Das Angebot muss den Bedarf nicht ganz decken. Reicht es nur fuer einen
  Teil, laeuft das Geraet trotzdem - der Rest kommt zum Marktpreis aus dem
  Netz, und der Mischpreis liegt immer noch unter {markt:.3f}.
- menge_wh ist die Menge, die du AUS DEM ANGEBOT beziehst: der
  Energiebedarf des Geraets, oder die angebotenen {menge} Wh, je nachdem
  was kleiner ist. Nie mehr als eines von beiden.
- Weniger als ein Viertel des Bedarfs lohnt den Aufwand nicht - dann lehne ab.
- Passt kein Geraet, lehne ab: annehmen = false, last = null.
- Wenn du annimmst, MUSST du ein Geraet benennen. annehmen = true mit
  last = null ist ungueltig.

Antworte NUR mit diesem JSON, ohne Text davor oder danach:
{{"annehmen": true oder false, "last": "geraetename" oder null,
"menge_wh": Zahl, "begruendung": "ein kurzer Satz"}}"""


def _frage_agent(agent, angebot, modell):
    """
    Holt eine Entscheidung vom Sprachmodell.

    Gibt None zurueck, wenn keine verwertbare Antwort kam - der Aufrufer
    faellt dann auf das Regelwerk zurueck.
    """
    geraete = "\n".join(f"  - {l.beschreibung()}" for l in agent.lasten) \
        or "  (keine)"

    prompt = PROMPT.format(
        haus=agent.id, geraete=geraete,
        verkaeufer=angebot.verkaeufer, menge=angebot.menge_wh,
        stunde=angebot.stunde, preis=angebot.preis_pro_kwh,
        markt=agent.markt_preis)

    antwort = frage(
        prompt, modell=modell,
        schema=["annehmen", "last", "menge_wh"])

    if antwort is None:
        return None
    if not antwort.get("annehmen"):
        return False       # bewusste Ablehnung, kein Fehler

    return Zusage(
        kaeufer=agent.id,
        last_name=antwort.get("last"),
        menge_wh=int(antwort.get("menge_wh") or 0),
        begruendung=str(antwort.get("begruendung", ""))[:200],
    )


def verhandle_angebot(angebot, agenten, modus="regelbasiert",
                      modell="gemma4:e2b", protokoll=None):
    """
    Laesst alle Agenten auf ein Angebot antworten und verteilt die Menge.

    Anders als eine Auktion mit einem einzigen Gewinner wird hier so lange
    zugeteilt, bis die Menge aufgebraucht oder niemand mehr Verwendung
    dafuer hat. Der Grund ist physikalisch: Was nicht verbraucht wird,
    fliesst zum schlechteren Tarif ins Netz zurueck. Ein Rest von 700 Wh
    ist fuer den Verkaeufer wertlos, fuer einen zweiten Nachbarn aber die
    halbe Waschmaschine.

    Gibt eine Liste von (agent, zusage) zurueck - im Regelfall eine oder
    zwei Zuteilungen, bei grossen Angeboten auch mehr.
    """
    if protokoll is None:
        protokoll = []

    protokoll.append(f"\n{angebot.beschreibung()}")

    offen = angebot.menge_wh
    vergeben = []
    schon_dran = set()

    # Mehrere Vergaberunden, bis nichts mehr passt.
    while offen > 0:
        rest_angebot = Angebot(
            verkaeufer=angebot.verkaeufer,
            stunde=angebot.stunde,
            menge_wh=offen,
            preis_pro_kwh=angebot.preis_pro_kwh,
        )

        kandidaten = []

        for agent in agenten:
            if agent.id == angebot.verkaeufer:
                continue
            # Wer schon eine Zuteilung hat, ist in dieser Runde durch.
            # Sonst wuerde ein Haushalt mit vielen Geraeten das ganze
            # Angebot aufsaugen, bevor die Nachbarn drankommen.
            if agent.id in schon_dran:
                continue

            zusage = None
            quelle = "Regel"

            if modus == "llm":
                try:
                    antwort = _frage_agent(agent, rest_angebot, modell)
                    if antwort is False:
                        if not vergeben:
                            protokoll.append(f"  {agent.id}: lehnt ab")
                        continue
                    if antwort is not None:
                        zusage = antwort
                        quelle = "LLM"
                except LLMNichtErreichbar as e:
                    protokoll.append(f"  [{e}] - weiter regelbasiert")
                    modus = "regelbasiert"

            if zusage is None:
                zusage = agent.entscheide_regelbasiert(rest_angebot)
                quelle = "Regel"

            if zusage is None:
                if not vergeben:
                    protokoll.append(f"  {agent.id}: nichts Passendes")
                continue

            gueltig, ergebnis = agent.pruefe_zusage(zusage, rest_angebot)
            if not gueltig:
                protokoll.append(f"  {agent.id}: Zusage verworfen ({ergebnis})")
                if quelle == "LLM":
                    zusage = agent.entscheide_regelbasiert(rest_angebot)
                    if zusage is None:
                        continue
                    gueltig, ergebnis = agent.pruefe_zusage(zusage, rest_angebot)
                    if not gueltig:
                        continue
                    protokoll.append(f"  {agent.id}: stattdessen regelbasiert "
                                     f"{zusage.last_name}")
                else:
                    continue

            protokoll.append(f"  {agent.id}: bietet fuer {zusage.last_name} "
                             f"({zusage.menge_wh} Wh) - {zusage.begruendung}")
            kandidaten.append((agent, zusage))

        if not kandidaten:
            break

        # Zuschlag an den, der den groessten ANTEIL seines Bedarfs deckt -
        # nicht an die groesste absolute Menge.
        #
        # Der Unterschied ist wesentlich: Bei 1900 Wh will ein Haushalt
        # 1200 fuer seine ganze Waschmaschine, ein anderer 1900 als zwei
        # Drittel seines Autos. Nach der absoluten Menge gewinnt das Auto,
        # und es bleibt nichts uebrig. Nach dem Anteil gewinnt die
        # Waschmaschine, und die verbleibenden 700 Wh gehen an das Auto -
        # beide bekommen etwas, und das Angebot ist vollstaendig genutzt.
        def anteil(paar):
            agent, z = paar
            last = next((l for l in agent.lasten if l.name == z.last_name), None)
            if last is None or last.energie_wh <= 0:
                return 0.0
            return z.menge_wh / last.energie_wh

        gewinner, zusage = max(kandidaten, key=lambda k: (anteil(k), k[1].menge_wh))
        gewinner.uebernehme(zusage, rest_angebot)
        protokoll.append(f"  -> Zuschlag an {gewinner.id} fuer "
                         f"{zusage.menge_wh} Wh")
        vergeben.append((gewinner, zusage))
        schon_dran.add(gewinner.id)
        offen -= zusage.menge_wh

    if not vergeben:
        protokoll.append("  -> niemand nimmt an")
    elif offen > 0:
        protokoll.append(f"  {offen} Wh bleiben uebrig und gehen ins Netz")

    return vergeben


def verhandlungsrunde(agenten, ueberschuesse, markt_preis=0.10,
                      rabatt=0.2, modus="regelbasiert",
                      modell="gemma4:e2b"):
    """
    Eine komplette Runde: Jeder Haushalt mit Ueberschuss macht ein Angebot.

    ueberschuesse: Liste von (agent_id, stunde, menge_wh)
    rabatt:        Anteil unter Marktpreis, zu dem angeboten wird

    Der Rabatt ist der Anreiz: Ein Produzent verkauft lieber guenstiger an
    einen Nachbarn, als den Ueberschuss ungenutzt zu lassen. Fuer den
    Kaeufer lohnt sich das Verschieben nur, wenn er dadurch spart.
    """
    protokoll = []
    vereinbarungen = []

    for verkaeufer_id, stunde, menge in ueberschuesse:
        if menge <= 0:
            continue
        angebot = Angebot(
            verkaeufer=verkaeufer_id,
            stunde=stunde,
            menge_wh=menge,
            preis_pro_kwh=markt_preis * (1 - rabatt),
        )
        for gewinner, zusage in verhandle_angebot(
                angebot, agenten, modus=modus, modell=modell,
                protokoll=protokoll):
            vereinbarungen.append((angebot, gewinner, zusage))

    return vereinbarungen, protokoll


# ==================================================================
#  Hauptprogramm
# ==================================================================


import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HIER = Path(__file__).parent
sys.path.insert(0, str(HIER))

from energy_agents import (HouseholdAgent, standard_lasten,
                           Angebot, Zusage)                        # noqa: E402
from calendar_loader import lade_ics, tagesmuster, passe_lasten_an  # noqa: E402

load_dotenv(HIER.parent / ".env")

CONFIG_PATH = HIER.parent / "config.json"
ABI_DIR = HIER.parent / "abi"
KALENDER_DIR = HIER / "kalender"
PROTOKOLL_DIR = HIER / "protokolle"

# Welcher Haushalt bekommt einen Agenten und welche Geraete?
# house_03 bleibt bewusst ohne - das ist die Kontrollgruppe, die die
# Challenge als Vergleich verlangt ("1 Haushalt mit Incentive vs 1 ohne").
ROLLEN = {
    "house_01": "produzent",
    "house_02": "teilflexibel",
    "house_03": None,            # kein Agent
    "house_04": "flexibel",
    "house_05": "produzent",
    "house_06": None,            # kein Agent
}



# Szenario fuer den Demo-Modus: sechs Haushalte, verschiedene Rollen,
# Angebote ueber den Tag verteilt. Gedacht fuer die Praesentation - laeuft
# ohne Blockchain und ohne Wartezeit, zeigt aber die volle Bandbreite der
# Verhandlung: grosse und kleine Angebote, verschiedene Zeitfenster, und
# Haushalte, die aus unterschiedlichen Gruenden ablehnen.

DEMO_HAUSHALTE = [
    {"id": "house_01", "address": "0x01", "base_consumption_kwh_per_hour": 0.4},
    {"id": "house_02", "address": "0x02", "base_consumption_kwh_per_hour": 0.6},
    {"id": "house_03", "address": "0x03", "base_consumption_kwh_per_hour": 0.6},
    {"id": "house_04", "address": "0x04", "base_consumption_kwh_per_hour": 0.7},
    {"id": "house_05", "address": "0x05", "base_consumption_kwh_per_hour": 0.5},
    {"id": "house_06", "address": "0x06", "base_consumption_kwh_per_hour": 0.5},
]

# (Sim-Stunde, [(Verkaeufer, Menge)]) - nachgebildet aus einem echten
# Tagesverlauf: morgens wenig, mittags viel, nachmittags abklingend.
DEMO_ANGEBOTE = [
    (9.0,  [("house_01", 1400)]),
    (11.0, [("house_01", 2600), ("house_05", 900)]),
    (13.0, [("house_01", 3100), ("house_02", 1500), ("house_05", 1200)]),
    (15.0, [("house_02", 2100), ("house_05", 800)]),
    (17.0, [("house_01", 1300)]),
    (19.0, [("house_02", 400)]),
]

# Zweites Szenario: die Nacht. Zeigt das Zusammenspiel von Speicher und
# Frist. Tagsueber hat house_01 seine Batterie mit PV-Ueberschuss gefuellt;
# nachts gibt sie diese Energie wieder ab. Auf der anderen Seite steht ein
# E-Auto, das um 6 Uhr abfahrbereit sein muss - der Agent muss also nicht
# nur guenstig einkaufen, sondern rechtzeitig fertig werden.
DEMO_NACHT = [
    (18.0, [("house_01", 700)]),      # Auto kommt an, Angebot zu klein
    (20.0, [("house_01", 1900)]),     # immer noch zu wenig fuer die Ladung
    (22.0, []),                        # niemand bietet etwas an
    (0.0,  [("house_01", 2900)]),     # Batterie gibt ab: jetzt passt es
    (2.0,  [("house_05", 3000)]),     # zweite Gelegenheit, Frist rueckt naeher
    (4.0,  []),                        # letzte Chance - Notladung greift
]


# ─────────────────────────────────────────────────────────────────────
#  Blockchain
# ─────────────────────────────────────────────────────────────────────

class Chain:
    """Duenner Wrapper um die Contract-Aufrufe, die wir brauchen."""

    def __init__(self, config):
        bc = config["blockchain"]
        self.bc = bc
        self.w3 = Web3(Web3.HTTPProvider(bc["rpc_url"]))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        key = os.getenv("AI_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
        if not key:
            raise RuntimeError("AI_PRIVATE_KEY oder DEPLOYER_PRIVATE_KEY fehlt")
        self.key = key
        self.account = self.w3.eth.account.from_key(key)

        def abi(name):
            with open(ABI_DIR / f"{name}.json") as f:
                return json.load(f)["abi"]

        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(bc["oracle_storage_address"]),
            abi=abi("OracleStorage"))
        self.market = self.w3.eth.contract(
            address=Web3.to_checksum_address(bc["p2p_market_address"]),
            abi=abi("P2PEnergyMarket"))
        self.ic = self.w3.eth.contract(
            address=Web3.to_checksum_address(bc["incentive_controller_address"]),
            abi=abi("IncentiveController"))

    def slot(self):
        return self.oracle.functions.getCurrentSlot().call()

    def meter(self, adresse):
        r = self.oracle.functions.getLatestMeterReading(
            Web3.to_checksum_address(adresse)).call()
        return int(r[0]), int(r[1])       # Verbrauch, Produktion

    def wetter(self):
        w = self.oracle.functions.getLatestWeather().call()
        return {"strahlung": int(w[0]), "temperatur": int(w[1]),
                "wolken": int(w[2])}

    def _send(self, fn, label):
        try:
            tx = fn.build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(
                    self.account.address, "pending"),
                "chainId": self.bc["chain_id"],
                "gas": int(fn.estimate_gas({"from": self.account.address}) * 1.5),
                "maxFeePerGas": self.w3.to_wei("30", "gwei"),
                "maxPriorityFeePerGas": self.w3.to_wei("2", "gwei"),
            })
            signed = self.w3.eth.account.sign_transaction(tx, self.key)
            h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            rec = self.w3.eth.wait_for_transaction_receipt(h, timeout=180)
            return rec.status == 1
        except Exception as e:
            print(f"      Fehler bei {label}: {type(e).__name__}")
            return False

    def prognose(self, adresse, slot, verbrauch, produktion):
        return self._send(self.ic.functions.submitForecast(
            Web3.to_checksum_address(adresse), slot,
            int(verbrauch), int(produktion)), "submitForecast")

    def ist_wert(self, adresse, slot, verbrauch, produktion):
        return self._send(self.ic.functions.submitActual(
            Web3.to_checksum_address(adresse), slot,
            int(verbrauch), int(produktion)), "submitActual")

    def score(self, adresse):
        return self.ic.functions.getReputationScore(
            Web3.to_checksum_address(adresse)).call()

    def multiplikator(self, adresse):
        return self.ic.functions.getPriceMultiplier(
            Web3.to_checksum_address(adresse)).call()

    def preis(self, adresse, wh=1000):
        return self.market.functions.calculateCostFor(
            Web3.to_checksum_address(adresse), wh).call()


# ─────────────────────────────────────────────────────────────────────
#  Agenten aufbauen
# ─────────────────────────────────────────────────────────────────────

def baue_agenten(config, heute=None):
    """
    Erzeugt einen Agenten je Haushalt und passt die Lastfenster anhand
    des Kalenders an.
    """
    heute = heute or date.today()
    agenten, ohne_agent = [], []

    for h in config["households"]:
        rolle = ROLLEN.get(h["id"])
        if rolle is None:
            ohne_agent.append(h)
            continue

        lasten = standard_lasten(rolle)

        ics = KALENDER_DIR / f"{h['id']}.ics"
        if ics.exists():
            muster = tagesmuster(lade_ics(ics), heute)
            lasten = passe_lasten_an(lasten, muster)
            if muster["termine"]:
                titel = ", ".join(t for _, t in muster["termine"][:2])
                print(f"  {h['id']}: {titel}")

        agenten.append(HouseholdAgent(h["id"], h["address"], lasten))

    return agenten, ohne_agent


def persistenz_prognose(letzter_verbrauch, haushalt):
    """
    Prognostiziert den Verbrauch des naechsten Slots als den des letzten.

    Das ist eine Persistenzprognose - in der Energieprognostik der uebliche
    Vergleichsmassstab, an dem sich jedes komplexere Modell messen lassen
    muss. Fuer unseren Fall ist sie sogar nahe am Optimum: Der Verbrauch
    aendert sich innerhalb eines Tageszeitfensters nur um das Rauschen des
    Simulators, also rund 7.5 Prozent im Mittel.

    Ein Agent kann das, weil er den Zaehler seines eigenen Hauses liest.
    Wer keinen Agenten hat, kennt nur einen groben Tagesdurchschnitt.
    """
    if letzter_verbrauch > 0:
        return letzter_verbrauch
    # Beim allerersten Durchlauf noch kein Messwert vorhanden
    return int(haushalt.get("base_consumption_kwh_per_hour", 0.5) * 0.25 * 1000)


def pauschal_prognose(haushalt):
    """
    Schaetzung ohne Agent: der Tagesdurchschnitt, ohne Tageszeitprofil.

    Wer sein Haus nicht misst, meldet einen Mittelwert. Nachts liegt er
    damit zu hoch, abends deutlich zu niedrig - genau die Abweichung, die
    der Reputationsscore bestraft.
    """
    basis = haushalt.get("base_consumption_kwh_per_hour", 0.5)
    # Mittlerer Tagesfaktor ueber 24 Stunden, aus dem Lastprofil gemittelt
    mittlerer_faktor = 1.25
    return int(basis * mittlerer_faktor * 0.25 * 1000)


# ─────────────────────────────────────────────────────────────────────
#  Hauptschleife
# ─────────────────────────────────────────────────────────────────────

def main():
    modus = "regelbasiert"
    zyklen = 5
    trocken = False
    demo = False
    nacht = False

    for arg in sys.argv[1:]:
        if arg == "--llm":
            modus = "llm"
        elif arg == "--trocken":
            trocken = True
        elif arg == "--demo":
            trocken = True
            demo = True
        elif arg == "--nacht":
            trocken = True
            demo = True
            nacht = True
        else:
            zyklen = int(arg)

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    szenario = DEMO_NACHT if nacht else DEMO_ANGEBOTE

    if demo:
        config = dict(config)
        config["households"] = DEMO_HAUSHALTE
        zyklen = min(zyklen, len(szenario))
        if nacht:
            print("Nacht-Szenario: der Speicher gibt ab, das E-Auto muss "
                  "bis 6 Uhr geladen sein.\n")
        else:
            print("Demo-Modus: sechs Haushalte, Angebote ueber den Tag "
                  "verteilt.\n")

    if modus == "llm":
        if verfuegbar():
            print("Ollama erreichbar, Verhandlung laeuft ueber das Modell.\n")
        else:
            print("Ollama nicht erreichbar - Verhandlung laeuft regelbasiert.")
            print("Starte 'ollama serve', um die LLM-Variante zu nutzen.\n")
            modus = "regelbasiert"

    chain = None if trocken else Chain(config)
    if chain:
        print(f"Submitter: {chain.account.address}")
        print(f"Controller: {chain.ic.address}\n")

    haushalte = {h["id"]: h for h in config["households"]}
    protokoll_gesamt = []

    for zyklus in range(1, zyklen + 1):
        print(f"\n{'=' * 62}")
        print(f"  Zyklus {zyklus} von {zyklen}")
        print(f"{'=' * 62}")

        agenten, ohne_agent = baue_agenten(config)

        # Aktuelle Lage vom Oracle holen
        if chain:
            slot = chain.slot()
            wetter = chain.wetter()
            print(f"\nSlot {slot}, {wetter['strahlung']} W/m2, "
                  f"{wetter['wolken']}% Wolken")
        else:
            slot = 1000 + zyklus
            if not demo:
                print(f"\nTrockenlauf, Slot {slot}")
            else:
                print()

        # Ueberschuesse ermitteln
        ueberschuesse = []
        stunde = 13.0     # Angebotszeitpunkt: Mittagsspitze

        if demo:
            stunde, angebote = szenario[(zyklus - 1) % len(szenario)]
            print(f"Simulationszeit {stunde:g} Uhr")
            if not angebote:
                print("  Niemand hat etwas anzubieten.")
            for wer, menge in angebote:
                ueberschuesse.append((wer, stunde, menge))
                print(f"  {wer}: {menge} Wh Ueberschuss")
        else:
            for a in agenten:
                if chain:
                    verbrauch, produktion = chain.meter(a.adresse)
                    netto = produktion - verbrauch
                else:
                    netto = 2000 if a.id in ("house_01", "house_05") else -400
                if netto > 0:
                    ueberschuesse.append((a.id, stunde, netto))
                    print(f"  {a.id}: {netto} Wh Ueberschuss")

        if not ueberschuesse:
            print("  Kein Ueberschuss - nichts zu verhandeln.")

        # ── Verhandlung ───────────────────────────────────────────
        print(f"\n--- Verhandlung ({modus}) ---")
        t0 = time.time()
        vereinbarungen, protokoll = verhandlungsrunde(
            agenten, ueberschuesse, modus=modus)
        dauer = time.time() - t0
        # Wer eine Frist hat und nichts bekommen hat, muss jetzt selbst
        # laden - zum vollen Netzpreis. Der Agent spart dann nichts, haelt
        # aber seine Zusage und seine Prognose ein.
        for a in agenten:
            for last in a.notladung(stunde):
                protokoll.append(
                    f"\n  {a.id}: laedt {last.name} ({last.energie_wh} Wh) "
                    f"zum vollen Preis - Frist {last.spaetestens:g} Uhr, "
                    f"kein guenstiges Angebot mehr abzuwarten")

        print("\n".join(protokoll))
        print(f"\n  {len(vereinbarungen)} Vereinbarungen in {dauer:.1f}s")
        protokoll_gesamt.extend([f"### Zyklus {zyklus}"] + protokoll)

        if not chain:
            continue

        # ── Prognosen einreichen ──────────────────────────────────
        print("\n--- Prognosen ---")
        prognosen = {}

        for a in agenten:
            h = haushalte[a.id]
            verbrauch_jetzt, produktion = chain.meter(a.adresse)
            basis = persistenz_prognose(verbrauch_jetzt, h)
            # Der Agent kennt seinen eigenen Fahrplan und rechnet die
            # verschobenen Lasten obendrauf.
            erwartet = a.prognose_verbrauch(basis, stunde)
            prognosen[a.id] = (erwartet, produktion)
            print(f"  {a.id}: {erwartet} Wh erwartet (Agent misst selbst)")
            chain.prognose(a.adresse, slot, erwartet, produktion)

        for h in ohne_agent:
            # Ohne Agent nur eine pauschale Schaetzung ueber den Tag -
            # ohne Tageszeitprofil und ohne Kenntnis laufender Geraete.
            grob = pauschal_prognose(h)
            _, produktion = chain.meter(h["address"])
            prognosen[h["id"]] = (grob, produktion)
            print(f"  {h['id']}: {grob} Wh geschaetzt (Tagesmittel, ohne Agent)")
            chain.prognose(h["address"], slot, grob, produktion)

        # ── Auf den Slot warten ───────────────────────────────────
        print("\n  Warte auf den naechsten Slot ...")
        while chain.slot() <= slot:
            time.sleep(10)

        # ── Ist-Werte nachtragen ──────────────────────────────────
        print("\n--- Ist-Werte ---")
        for hid, (erwartet, _) in prognosen.items():
            adresse = haushalte[hid]["address"]
            verbrauch, produktion = chain.meter(adresse)
            abweichung = abs(verbrauch - erwartet) / max(erwartet, 1) * 100
            print(f"  {hid}: {verbrauch} Wh real, "
                  f"{abweichung:.0f}% Abweichung")
            chain.ist_wert(adresse, slot, verbrauch, produktion)

        # ── Ergebnis ──────────────────────────────────────────────
        print("\n--- Reputation und Preis ---")
        print("  Haushalt    Agent  Score  Preis fuer 1 kWh")
        for hid in prognosen:
            adresse = haushalte[hid]["address"]
            hat_agent = "ja " if ROLLEN.get(hid) else "nein"
            print(f"  {hid:10s}  {hat_agent}   {chain.score(adresse):5d}"
                  f"  {chain.preis(adresse):>10d}")

    # ── Protokoll sichern ─────────────────────────────────────────
    if protokoll_gesamt:
        PROTOKOLL_DIR.mkdir(exist_ok=True)
        pfad = PROTOKOLL_DIR / f"verhandlung_{int(time.time())}.txt"
        pfad.write_text("\n".join(protokoll_gesamt), encoding="utf-8")
        print(f"\nProtokoll gespeichert: {pfad}")


if __name__ == "__main__":
    main()
