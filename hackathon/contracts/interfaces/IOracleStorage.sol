// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IOracleStorage
 * @notice Interface zum Lesen aller Mess- und Wetterdaten.
 * @dev Teams importieren dieses Interface in ihre Contracts und rufen die Getter auf.
 *      Schreiben in den Oracle erfolgt ausschliesslich durch das Python-Skript.
 */
interface IOracleStorage {
    struct MeterReading {
        uint256 consumptionWh;   // Verbrauch in Wattstunden
        uint256 productionWh;    // PV-Erzeugung in Wattstunden
        uint256 timestamp;       // Slot-Timestamp
    }

    struct BatteryState {
        uint256 socPercent;      // State of Charge in % (0-100)
        uint256 capacityWh;      // Kapazität in Wattstunden
        uint256 maxRateWh;       // Max. Lade-/Entladerate pro Slot
        uint256 timestamp;
    }

    struct WeatherData {
        uint256 irradianceWm2;   // Solarstrahlung in W/m²
        int256  temperatureC;    // Temperatur in °C * 10 (z.B. 235 = 23.5°C)
        uint256 cloudCover;      // Bewölkung 0-100%
        uint256 timestamp;
    }

    function getLatestMeterReading(address household) external view returns (MeterReading memory);
    function getLatestBatteryState(address household) external view returns (BatteryState memory);
    function getLatestWeather() external view returns (WeatherData memory);

    function getCurrentSlot() external view returns (uint256);
    function isHouseholdRegistered(address household) external view returns (bool);
}
