# Phase 3: Agenten, Verhandlung und Reputation

Jeder Haushalt bekommt einen Agenten, der seine verschiebbaren Geräte kennt
und mit den Nachbarn über günstigen Strom verhandelt. Wer seinen Verbrauch
vorher meldet und dann trifft, baut Reputation auf und zahlt weniger.

---

## Schnellstart

Aus dem Ordner `python/Phase 3`, mit aktiviertem venv:

```powershell
..\venv\Scripts\Activate.ps1

python test_agents.py           # 43 Tests, alle sollten durchlaufen
python run_agents.py --nacht 6  # Nachtszenario, ohne Blockchain
python build_dashboard.py       # erzeugt dashboard.html
```

`dashboard.html` per Doppelklick öffnen — die Datei ist eigenständig und
braucht kein Internet.

---

## Dateien

| Datei | Wofür |
|---|---|
| `energy_agents.py` | Der Agent selbst: Geräte, Entscheidung, Prüfschicht |
| `calendar_loader.py` | Liest ICS-Kalender und leitet Zeitfenster ab |
| `run_agents.py` | Hauptskript: LLM-Anbindung, Verhandlung, On-Chain |
| `build_dashboard.py` | Erzeugt die Präsentationsseite aus Protokoll und Chain |
| `test_agents.py` | 43 Tests |
| `kalender/*.ics` | Beispielkalender, in Outlook und Google importierbar |
| `protokolle/` | Verhandlungsprotokolle, je Lauf eine Datei |

Dazu im übergeordneten Ordner: `setup_phase3.py` verknüpft den
IncentiveController mit dem Market und autorisiert die Adresse, die
Prognosen einreicht.

---

## Betriebsarten

### Ohne Blockchain

```powershell
python run_agents.py --nacht 6
```

Nachtszenario: Das E-Auto kommt um 18 Uhr an und muss bis 6 Uhr geladen
sein. Zeigt Frist, Teilmengen und Notladung. Läuft in Sekunden durch.

```powershell
python run_agents.py --demo 6
```

Tagesszenario: Angebote über den Tagesverlauf, mehrere Haushalte
konkurrieren um dieselbe Energie.

Beide Szenarien verwenden vorgegebene Überschussmengen. Das ist für die
Vorführung gedacht, damit man nicht auf passende Wetterlagen warten muss —
im Pitch entsprechend benennen.

### Mit lokalem Sprachmodell

```powershell
python run_agents.py --nacht --llm 6
```

Braucht ein laufendes Ollama mit `gemma4:e2b`. Prüfen mit:

```powershell
Invoke-WebRequest http://localhost:11434/api/tags -UseBasicParsing
```

Rechne mit etwa 80 Sekunden je Verhandlungsrunde. Antwortet Ollama nicht,
fällt das Skript automatisch auf das Regelwerk zurück.

### Mit Blockchain

```powershell
python run_agents.py 12
```

Liest die Überschüsse vom Oracle, reicht Prognosen ein, wartet einen Slot ab
und trägt die Ist-Werte nach. Der Contract berechnet daraus die Reputation.

**Voraussetzung:** `oracle_writer.py` und `settlement_trigger.py` müssen
parallel laufen, sonst gibt es keine neuen Slots. Zwölf Zyklen dauern etwa
fünfzehn Minuten.

---

## Wie der Agent entscheidet

**Zeitfenster.** Jedes Gerät hat ein Fenster, in dem es laufen darf. Die
Waschmaschine zwischen 8 und 20 Uhr, das E-Auto zwischen 18 und 6 Uhr. Der
Kalender verschiebt diese Fenster: Wer bis 17 Uhr im Büro ist, kann mittags
nichts starten.

**Angebot annehmen.** Ein Angebot liegt 20 Prozent unter Marktpreis. Der
Agent nimmt an, wenn ein Gerät ins Fenster passt und mindestens ein Viertel
seines Bedarfs gedeckt wird.

**Teilmengen.** Das Angebot muss den Bedarf nicht ganz decken. Deckt der
Nachbar 800 von 1200 Wh, kommen die restlichen 400 aus dem Netz — der
Mischpreis liegt trotzdem darunter. Physikalisch ist das ohnehin so: Strom
kommt nicht in Portionen aus bestimmten Leitungen.

**Fristen.** Manche Geräte müssen bis zu einer Uhrzeit gelaufen sein. Rückt
die Frist näher, nimmt der Agent auch ein Angebot ohne Preisvorteil. Bleibt
weniger Zeit als die Ladedauer, lädt er ohne jedes Angebot zum vollen
Netzpreis — er spart dann nichts, hält aber seine Prognose ein.

**Zuschlag.** Bei mehreren Interessenten gewinnt, wer den grössten *Anteil*
seines Bedarfs deckt, nicht die grösste absolute Menge. Sonst nähme ein
E-Auto mit 2800 Wh Bedarf jedes Angebot, und für die Waschmaschine des
Nachbarn bliebe nichts. Die Restmenge geht an den nächsten.

---

## Die Prüfschicht

Das Sprachmodell entscheidet mit, aber es setzt nichts durch. Jede Zusage
läuft durch `pruefe_zusage()` und wird verworfen, wenn sie gegen eine dieser
Regeln verstösst:

- Gerät existiert nicht oder ist schon eingeplant
- Uhrzeit liegt ausserhalb des Gerätefensters
- Menge übersteigt das Angebot oder den Gerätebedarf
- Menge deckt weniger als ein Viertel des Bedarfs

Wird eine Zusage verworfen, übernimmt das Regelwerk. In einem dokumentierten
Lauf hat das Modell dreimal etwas Unmögliches vorgeschlagen — eine
Waschmaschine um 2 Uhr nachts und zweimal eine unpassende Menge. Alle drei
wurden abgefangen, nachzulesen in `protokolle/`.

---

## Prognose und Reputation

Ein Haushalt mit Agent meldet vor jedem Slot, wieviel er verbrauchen wird:
den letzten Messwert plus die selbst eingeplanten Lasten. Das ist eine
Persistenzprognose — in der Energieprognostik der übliche Vergleichsmassstab,
und hier nahe am Optimum, weil sich der Verbrauch innerhalb eines
Zeitfensters nur um das Rauschen des Simulators ändert.

Ein Haushalt ohne Agent meldet einen pauschalen Tagesdurchschnitt. Der
trifft zweimal am Tag zufällig, sonst nicht.

Der Contract vergleicht Prognose und Ist-Wert und rechnet daraus einen Score
zwischen 0 und 1000, geglättet über acht Slots. Daraus wird ein
Preismultiplikator: 800 sind 20 Prozent Rabatt, 1200 sind 20 Prozent
Aufschlag. Neueinsteiger starten neutral bei 500.

---

## Contracts auf Sepolia

| Contract | Adresse |
|---|---|
| OracleStorage | `0x61769c1C299495194D7Df49030019e613d13a88D` |
| P2PEnergyMarket | `0x1c33292F3Fe9720C78606A1bB714E7D8FEd37E02` |
| BatteryManager | `0x2d074bC0c484b5012ceA2D2d1378297BF18802eb` |
| IncentiveController | `0x8A4F38Da46371C706DAC7a77AD128542AE2Dc638` |

Alle auf Etherscan verifiziert.

---

## Neu aufsetzen

Falls der IncentiveController neu deployt werden muss — etwa weil die
Score-Historie unbrauchbare Daten enthält:

```powershell
cd ..                                    # nach python/
python deploy_helper.py IncentiveController
```

Adresse in `config.json` unter `incentive_controller_address` eintragen,
dann bei gestopptem Oracle:

```powershell
python setup_phase3.py
```

Danach Oracle und Trigger starten und einen Lauf mit zwölf Zyklen fahren,
damit sich die Reputation aufbaut.

---

## Bekannte Fallstricke

**Nonce-Konflikte.** Oracle-Writer und Deploy-Skripte senden vom selben
Konto. Vor jedem Deploy und vor `setup_phase3.py` den Oracle stoppen.

**Der AI-Submitter braucht ein eigenes Konto.** In `.env` steht
`AI_PRIVATE_KEY`. Zeigt es auf dasselbe Konto wie der Oracle, kollidieren
die Transaktionen und etwa die Hälfte der Einreichungen schlägt fehl.

**Nachts gibt es keinen Überschuss.** Ein Lauf mit Blockchain zwischen
Sim-Stunde 20 und 8 zeigt keine Verhandlung. Für einen lebendigen Dialog
braucht es Sim-Stunde 10 bis 14 — oder eines der Demo-Szenarien.

**Die Sim-Zeit springt beim Neustart des Oracle** auf Mitternacht zurück.
Bis zur Mittagsspitze dauert es dann etwa vierzig Minuten.

**`in-flight transaction limit`** beim Settlement-Trigger ist ein bekannter
Effekt delegierter Konten auf Sepolia. Er behebt sich nach ein bis zwei
Slots von selbst.
