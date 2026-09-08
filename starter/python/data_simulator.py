"""
data_simulator.py

VOLLSTÄNDIG VORGEGEBEN - Teams modifizieren dieses Skript NICHT.

Generiert synthetische Mess-Daten für alle Haushalte in der Simulation:
  - Smart Meter (Verbrauch in Wh)
  - PV-Erzeugung (in Wh)
  - Batterie-Zustand (SoC in %)
  - Wetterdaten (Strahlung W/m², Temperatur, Bewölkung)

Realistischer Tagesgang:
  - Sonnenaufgang ~06:00 simuliert, Untergang ~20:00 (skaliert auf Simulationszeit)
  - Verbrauch mit Morgen- und Abendspitze
  - Wetter mit zufälligen Wolkendurchgängen

Datenausgabe:
  - In-Memory: get_current_readings() liefert Dict mit aktuellen Werten
  - Für AI-Phase 3: SQLite-DB mit Historie unter data/history.db
"""

import json
import math
import random
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

CONFIG_PATH = Path(__file__).parent / "config.json"
DB_PATH = Path(__file__).parent.parent / "data" / "history.db"

# Echtzeit-Slot = 1 Minute, repräsentiert 15 Minuten Simulationszeit
SIM_MINUTES_PER_SLOT = 15


@dataclass
class HouseholdReading:
    household_id: str
    address: str
    consumption_wh: int
    production_wh: int
    battery_soc: int          # in Prozent
    battery_capacity_wh: int
    battery_max_rate_wh: int
    timestamp: int            # Unix


@dataclass
class WeatherReading:
    irradiance_wm2: int
    temperature_c_x10: int    # *10 für int-Übergabe an Solidity
    cloud_cover: int          # 0-100
    timestamp: int


# ─────────────────────────────────────────────────────────────────────
#  Simulator
# ─────────────────────────────────────────────────────────────────────

class EnergySimulator:
    """
    Simuliert Energiedaten für eine konfigurierbare Anzahl Haushalte.

    Zeitmodell:
      - 1 reale Minute = 15 Simulationsminuten
      - Ein Sim-Tag (24h) entspricht also 96 reale Minuten = 1.6h
    """

    def __init__(self, config_path: Path = CONFIG_PATH):
        with open(config_path) as f:
            self.config = json.load(f)

        # Hinweis: start_real_time und battery_soc leben nur im Prozessspeicher
        # dieser Instanz - bei jedem Neustart von oracle_writer.py (der diese
        # Klasse instanziiert) beginnt die simulierte Uhrzeit wieder bei 0 und
        # der SoC wieder bei 50%. Das ist eine bekannte Einschränkung der
        # Simulation, kein Bug. Persistenz über Neustarts hinweg gibt es
        # bewusst nicht (siehe README, Abschnitt "Tipps").
        self.start_real_time = time.time()
        # SoC pro Haushalt im Speicher tracken (initial 50%)
        self.battery_soc: Dict[str, float] = {
            h["id"]: 50.0 for h in self.config["households"]
        }
        self._init_db()

    # ─────────────────────────────────────────────────────────────
    #  Persistenz
    # ─────────────────────────────────────────────────────────────

    def _init_db(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meter_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    consumption_wh INTEGER NOT NULL,
                    production_wh INTEGER NOT NULL,
                    battery_soc INTEGER,
                    sim_hour REAL,
                    sim_dayofweek INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_meter_household
                    ON meter_history(household_id, timestamp);

                CREATE TABLE IF NOT EXISTS weather_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    irradiance INTEGER NOT NULL,
                    temperature_c_x10 INTEGER NOT NULL,
                    cloud_cover INTEGER NOT NULL,
                    sim_hour REAL
                );
            """)

    # ─────────────────────────────────────────────────────────────
    #  Zeitberechnung
    # ─────────────────────────────────────────────────────────────

    def get_simulated_hour(self) -> float:
        """Gibt die simulierte Tageszeit zurück (0.0 - 24.0)."""
        elapsed_real_seconds = time.time() - self.start_real_time
        # 1 reale Minute = 15 Simulationsminuten = 0.25 Sim-Stunden
        sim_hours = elapsed_real_seconds / 60.0 * (SIM_MINUTES_PER_SLOT / 60.0)
        return sim_hours % 24

    def get_simulated_dayofweek(self) -> int:
        elapsed_real_seconds = time.time() - self.start_real_time
        sim_days = elapsed_real_seconds / 60.0 * (SIM_MINUTES_PER_SLOT / 60.0) / 24.0
        return int(sim_days) % 7

    # ─────────────────────────────────────────────────────────────
    #  Wetter
    # ─────────────────────────────────────────────────────────────

    def _generate_weather(self, sim_hour: float) -> WeatherReading:
        weather_cfg = self.config["weather"]

        # Strahlung: Sinus-Kurve, Maximum 13:00, 0 vor 6:00 und nach 20:00
        if 6 <= sim_hour <= 20:
            sun_position = (sim_hour - 6) / 14  # 0..1 zwischen 6-20 Uhr
            irradiance_max = weather_cfg["max_irradiance_wm2"]
            base_irradiance = irradiance_max * math.sin(math.pi * sun_position)
        else:
            base_irradiance = 0

        # Bewölkung mit langsamer Variation + Rauschen
        cloud_base = 30 + 30 * math.sin(sim_hour * 0.5)
        cloud_cover = max(0, min(100, int(cloud_base + random.gauss(0, 15))))

        # Strahlung durch Wolken reduzieren
        cloud_factor = 1 - (cloud_cover / 100) * 0.7
        irradiance = max(0, int(base_irradiance * cloud_factor))

        # Temperatur: Tagesgang
        temp_base = weather_cfg["base_temperature_c"]
        temp_variation = 8 * math.sin(math.pi * (sim_hour - 6) / 12) if 6 <= sim_hour <= 18 else -3
        temperature = temp_base + temp_variation + random.gauss(0, 1)

        return WeatherReading(
            irradiance_wm2=irradiance,
            temperature_c_x10=int(temperature * 10),
            cloud_cover=cloud_cover,
            timestamp=int(time.time())
        )

    # ─────────────────────────────────────────────────────────────
    #  Verbrauch / Produktion
    # ─────────────────────────────────────────────────────────────

    def _calculate_consumption(self, household_cfg: Dict, sim_hour: float) -> int:
        """Verbrauchsprofil: Morgenspitze 7-9, Abendspitze 18-22."""
        base_kwh = household_cfg["base_consumption_kwh_per_hour"]

        # Lastfaktor je nach Tageszeit
        if 7 <= sim_hour < 9:
            factor = 2.5  # Morgenspitze
        elif 12 <= sim_hour < 14:
            factor = 1.8  # Mittag
        elif 18 <= sim_hour < 22:
            factor = 3.0  # Abendspitze
        elif 23 <= sim_hour or sim_hour < 6:
            factor = 0.4  # Nacht
        else:
            factor = 1.0

        # Wattstunden für 15 Sim-Minuten
        kwh_in_slot = base_kwh * factor * (SIM_MINUTES_PER_SLOT / 60.0)
        # Plus Zufallsschwankung
        kwh_in_slot *= random.uniform(0.85, 1.15)

        return int(kwh_in_slot * 1000)

    def _calculate_production(self, household_cfg: Dict, weather: WeatherReading) -> int:
        """PV-Produktion abhängig von Strahlung."""
        peak_kwp = household_cfg["pv_peak_kwp"]
        if peak_kwp == 0:
            return 0

        # Effizienzformel: Produktion proportional zu Strahlung relativ zu 1000 W/m²
        production_kw = peak_kwp * (weather.irradiance_wm2 / 1000.0)
        kwh_in_slot = production_kw * (SIM_MINUTES_PER_SLOT / 60.0)

        # Leichtes Rauschen
        kwh_in_slot *= random.uniform(0.9, 1.05)

        return max(0, int(kwh_in_slot * 1000))

    # ─────────────────────────────────────────────────────────────
    #  Batterie-SoC update (vereinfacht)
    # ─────────────────────────────────────────────────────────────

    def _update_battery_soc(self, household_cfg: Dict, surplus_wh: int) -> int:
        """
        Vereinfachtes SoC-Modell: Überschuss lädt, Defizit entlädt.
        Reale Lade-/Entladelogik wird vom Team in BatteryManager.sol implementiert.
        Hier ist nur die physikalische Simulation.
        """
        h_id = household_cfg["id"]
        capacity_kwh = household_cfg["battery_capacity_kwh"]

        if capacity_kwh == 0:
            return 0

        capacity_wh = capacity_kwh * 1000
        max_rate_wh = household_cfg["battery_max_rate_kwh"] * 1000 * (SIM_MINUTES_PER_SLOT / 60.0)

        # Begrenzte Lade-/Entladerate
        delta_wh = max(-max_rate_wh, min(max_rate_wh, surplus_wh))

        current_wh = self.battery_soc[h_id] / 100 * capacity_wh
        new_wh = max(0, min(capacity_wh, current_wh + delta_wh))
        self.battery_soc[h_id] = (new_wh / capacity_wh) * 100

        return int(self.battery_soc[h_id])

    # ─────────────────────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────────────────────

    def get_current_readings(self) -> Dict:
        """Hauptfunktion: Liefert aktuelle Readings für alle Haushalte + Wetter."""
        sim_hour = self.get_simulated_hour()
        weather = self._generate_weather(sim_hour)

        readings = []
        for household_cfg in self.config["households"]:
            consumption = self._calculate_consumption(household_cfg, sim_hour)
            production = self._calculate_production(household_cfg, weather)
            surplus = production - consumption
            soc = self._update_battery_soc(household_cfg, surplus)

            reading = HouseholdReading(
                household_id=household_cfg["id"],
                address=household_cfg["address"],
                consumption_wh=consumption,
                production_wh=production,
                battery_soc=soc,
                battery_capacity_wh=int(household_cfg["battery_capacity_kwh"] * 1000),
                battery_max_rate_wh=int(household_cfg["battery_max_rate_kwh"] * 1000),
                timestamp=int(time.time())
            )
            readings.append(reading)
            self._persist_reading(reading, sim_hour)

        self._persist_weather(weather, sim_hour)

        return {
            "sim_hour": sim_hour,
            "sim_dayofweek": self.get_simulated_dayofweek(),
            "weather": asdict(weather),
            "households": [asdict(r) for r in readings]
        }

    # ─────────────────────────────────────────────────────────────

    def _persist_reading(self, reading: HouseholdReading, sim_hour: float):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO meter_history(household_id, timestamp, consumption_wh, "
                "production_wh, battery_soc, sim_hour, sim_dayofweek) VALUES (?,?,?,?,?,?,?)",
                (reading.household_id, reading.timestamp, reading.consumption_wh,
                 reading.production_wh, reading.battery_soc, sim_hour,
                 self.get_simulated_dayofweek())
            )

    def _persist_weather(self, weather: WeatherReading, sim_hour: float):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO weather_history(timestamp, irradiance, temperature_c_x10, "
                "cloud_cover, sim_hour) VALUES (?,?,?,?,?)",
                (weather.timestamp, weather.irradiance_wm2,
                 weather.temperature_c_x10, weather.cloud_cover, sim_hour)
            )


# ─────────────────────────────────────────────────────────────────────
#  Standalone-Modus: einfacher CLI-Test
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sim = EnergySimulator()
    print("Energy Simulator gestartet. Drücke Ctrl+C zum Beenden.\n")
    try:
        while True:
            data = sim.get_current_readings()
            print(f"\n=== Sim-Stunde: {data['sim_hour']:.2f}h "
                  f"(Tag: {data['sim_dayofweek']}) ===")
            w = data['weather']
            print(f"Wetter: {w['irradiance_wm2']} W/m² | "
                  f"{w['temperature_c_x10']/10:.1f}°C | "
                  f"Wolken: {w['cloud_cover']}%")
            for h in data['households']:
                net = h['production_wh'] - h['consumption_wh']
                print(f"  {h['household_id']}: "
                      f"Verbrauch={h['consumption_wh']}Wh | "
                      f"PV={h['production_wh']}Wh | "
                      f"Netto={net:+d}Wh | "
                      f"SoC={h['battery_soc']}%")
            time.sleep(60)  # Slot = 1 Minute
    except KeyboardInterrupt:
        print("\nSimulator gestoppt.")
