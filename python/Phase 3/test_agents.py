"""Test der Agentenschicht. Laeuft ohne Blockchain und ohne LLM."""
from energy_agents import (HouseholdAgent, FlexibleLoad, Angebot, Zusage,
                           standard_lasten)

ok_zaehler = 0
fehl_zaehler = 0


def pruefe(name, bedingung, detail=""):
    global ok_zaehler, fehl_zaehler
    if bedingung:
        ok_zaehler += 1
        print(f"  OK    {name}")
    else:
        fehl_zaehler += 1
        print(f"  FEHLT {name}  {detail}")


print("=== Zeitfenster ===")
wama = FlexibleLoad("waschmaschine", 1200, 8, 20)
eauto = FlexibleLoad("eauto", 8000, 18, 6)     # ueber Mitternacht

pruefe("Waschmaschine 14 Uhr im Fenster", wama.im_fenster(14))
pruefe("Waschmaschine 22 Uhr ausserhalb", not wama.im_fenster(22))
pruefe("E-Auto 22 Uhr im Fenster", eauto.im_fenster(22))
pruefe("E-Auto 3 Uhr im Fenster (nach Mitternacht)", eauto.im_fenster(3))
pruefe("E-Auto 14 Uhr ausserhalb", not eauto.im_fenster(14))

print("\n=== Regelbasierte Entscheidung ===")
a4 = HouseholdAgent("house_04", "0xAAA", standard_lasten("flexibel"))

# 14 Uhr: Waschmaschine (1200) und Waermepumpe (2000) im Fenster, E-Auto nicht
angebot = Angebot("house_01", 14, 2500, 0.08)
z = a4.entscheide_regelbasiert(angebot)
pruefe("nimmt Angebot an", z is not None)
pruefe("waehlt Waermepumpe (groesste passende Last)",
       z and z.last_name == "waermepumpe", f"gewaehlt: {z.last_name if z else None}")

# Angebot nicht guenstiger als der Markt
teuer = Angebot("house_01", 14, 2500, 0.10)
pruefe("lehnt Angebot zum Marktpreis ab",
       a4.entscheide_regelbasiert(teuer) is None)

# Teilmengen: ein Angebot muss nicht den ganzen Bedarf decken, aber
# Kleinstmengen lohnen den Aufwand nicht.
teilmenge = Angebot("house_01", 14, 500, 0.08)
z_teil = a4.entscheide_regelbasiert(teilmenge)
pruefe("nimmt Teilmenge an", z_teil is not None and z_teil.menge_wh == 500,
       f"ergab {z_teil}")

winzig = Angebot("house_01", 14, 100, 0.08)
pruefe("lehnt Kleinstmenge ab",
       a4.entscheide_regelbasiert(winzig) is None)

print("\n=== Validierung: was das LLM falsch machen koennte ===")
a = HouseholdAgent("house_04", "0xAAA", standard_lasten("flexibel"))
angebot = Angebot("house_01", 14, 2500, 0.08)

ok, grund = a.pruefe_zusage(Zusage("house_04", None, 800), angebot)
pruefe("verwirft Zusage ohne Last", not ok, grund)

ok, grund = a.pruefe_zusage(Zusage("house_04", "kernreaktor", 800), angebot)
pruefe("verwirft unbekannte Last", not ok, grund)

ok, grund = a.pruefe_zusage(Zusage("house_04", "eauto", 8000), angebot)
pruefe("verwirft Last ausserhalb ihres Fensters", not ok, grund)

ok, grund = a.pruefe_zusage(Zusage("house_04", "waschmaschine", 5000), angebot)
pruefe("verwirft falsche Menge", not ok, grund)

gross = Angebot("house_01", 14, 999999, 0.08)
ok, grund = a.pruefe_zusage(Zusage("house_04", "waschmaschine", 1200), gross)
pruefe("akzeptiert korrekte Zusage", ok, grund if not ok else "")

print("\n=== Doppelbuchung ===")
a.uebernehme(Zusage("house_04", "waschmaschine", 1200), angebot)
ok, grund = a.pruefe_zusage(Zusage("house_04", "waschmaschine", 1200), angebot)
pruefe("verwirft zweite Zusage derselben Last", not ok, grund)

print("\n=== Prognose ===")
# Waschmaschine ist jetzt fuer 14 Uhr eingeplant
pruefe("Prognose 14 Uhr enthaelt die Waschmaschine",
       a.prognose_verbrauch(300, 14) == 1500,
       f"ergab {a.prognose_verbrauch(300, 14)}")
pruefe("Prognose 10 Uhr nur Grundlast",
       a.prognose_verbrauch(300, 10) == 300,
       f"ergab {a.prognose_verbrauch(300, 10)}")

starr = HouseholdAgent("house_06", "0xFFF", standard_lasten("starr"))
pruefe("Haushalt ohne Geraete hat keine Flexibilitaet",
       not starr.hat_flexibilitaet())
pruefe("Haushalt ohne Geraete lehnt jedes Angebot ab",
       starr.entscheide_regelbasiert(angebot) is None)

print("\n=== Protokoll ===")
for zeile in a.protokoll:
    print("  " + zeile)


print("\n=== Lasten mit Frist (E-Auto) ===")

auto = FlexibleLoad("eauto", 2800, 18, 6, dauer_h=2.0, spaetestens=6.0)
pruefe("Frist 18 Uhr -> 12 h", auto.stunden_bis_frist(18) == 12)
pruefe("Frist 23 Uhr -> 7 h", auto.stunden_bis_frist(23) == 7)
pruefe("Frist 2 Uhr -> 4 h", auto.stunden_bis_frist(2) == 4)
pruefe("20 Uhr noch nicht dringend", not auto.dringend(20))
pruefe("3 Uhr dringend", auto.dringend(3))

wama = FlexibleLoad("waschmaschine", 1200, 8, 20)
pruefe("ohne Frist nie dringend", not wama.dringend(3))
pruefe("ohne Frist keine Reststunden", wama.stunden_bis_frist(3) is None)

# Bei knapper Frist wird auch ein Angebot ohne Ersparnis angenommen
d = HouseholdAgent("h", "0x0", standard_lasten("flexibel"))
z = d.entscheide_regelbasiert(Angebot("v", 3.0, 3000, d.markt_preis))
pruefe("Frist schlaegt fehlenden Preisvorteil",
       z is not None and z.last_name == "eauto")

e = HouseholdAgent("h", "0x0", standard_lasten("flexibel"))
pruefe("ohne Dringlichkeit kein Zuschlag ohne Vorteil",
       e.entscheide_regelbasiert(Angebot("v", 20.0, 3000, e.markt_preis)) is None)

print("\n=== Notladung ===")

f = HouseholdAgent("h", "0x0", standard_lasten("flexibel"))
pruefe("22 Uhr noch keine Notladung", f.notladung(22) == [])

g = HouseholdAgent("h", "0x0", standard_lasten("flexibel"))
faellig = g.notladung(4)
pruefe("4 Uhr Notladung faellig",
       len(faellig) == 1 and faellig[0].name == "eauto")
pruefe("Notladung setzt den Fahrplan",
       any(l.name == "eauto" and l.geplant_fuer == 4 for l in g.lasten))
pruefe("Notladung nur einmal", g.notladung(5) == [])


print("\n=== Teilmengen und Mischpreis ===")

tm = HouseholdAgent("h", "0x0", standard_lasten("teilflexibel"))
wama = next(l for l in tm.lasten if l.name == "waschmaschine")

pruefe("Bezugsmenge deckelt beim Bedarf",
       tm.bezugsmenge(wama, 5000) == 1200)
pruefe("Bezugsmenge bei Teilangebot",
       tm.bezugsmenge(wama, 800) == 800)

# 800 von 1200 Wh zu 0.08, Rest zu 0.10 -> (800*0.08 + 400*0.10)/1200
erwartet = (800 * 0.08 + 400 * 0.10) / 1200
pruefe("Mischpreis korrekt",
       abs(tm.mischpreis(wama, 800, 0.08) - erwartet) < 1e-9,
       f"ergab {tm.mischpreis(wama, 800, 0.08):.6f}, erwartet {erwartet:.6f}")
pruefe("Mischpreis bei Vollbezug gleich Angebotspreis",
       abs(tm.mischpreis(wama, 1200, 0.08) - 0.08) < 1e-9)
pruefe("Mischpreis liegt zwischen Angebot und Markt",
       0.08 < tm.mischpreis(wama, 600, 0.08) < 0.10)

# Pruefung: Teilmenge ja, mehr als Bedarf nein, Kleinstmenge nein
ang = Angebot("v", 14, 5000, 0.08)
ok, _ = tm.pruefe_zusage(Zusage("h", "waschmaschine", 600), ang)
pruefe("Pruefung akzeptiert Teilmenge", ok)

ok, grund = tm.pruefe_zusage(Zusage("h", "waschmaschine", 1500), ang)
pruefe("Pruefung verwirft Menge ueber Bedarf", not ok, grund)

ok, grund = tm.pruefe_zusage(Zusage("h", "waschmaschine", 200), ang)
pruefe("Pruefung verwirft Kleinstmenge", not ok, grund)

# Die Last wird trotz Teilbezug vollstaendig eingeplant
tv = HouseholdAgent("h", "0x0", standard_lasten("teilflexibel"))
tv.uebernehme(Zusage("h", "waschmaschine", 700), Angebot("v", 14, 700, 0.08))
gepl = next(l for l in tv.lasten if l.name == "waschmaschine")
pruefe("Teilbezug plant die Last trotzdem ganz ein",
       gepl.geplant_fuer == 14)
pruefe("Prognose enthaelt den vollen Bedarf",
       tv.prognose_verbrauch(300, 14) == 300 + 1200,
       f"ergab {tv.prognose_verbrauch(300, 14)}")

print(f"\n{ok_zaehler} bestanden, {fehl_zaehler} fehlgeschlagen")
