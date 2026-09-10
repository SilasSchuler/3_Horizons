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

# Angebot zu klein fuer jede Last
klein = Angebot("house_01", 14, 500, 0.08)
pruefe("lehnt zu kleines Angebot ab",
       a4.entscheide_regelbasiert(klein) is None)

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

print(f"\n{ok_zaehler} bestanden, {fehl_zaehler} fehlgeschlagen")
