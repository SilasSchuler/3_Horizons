// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../interfaces/IOracleStorage.sol";

/**
 * @title MockOracleStorage
 * @notice Test-Double: implementiert IOracleStorage mit frei setzbaren Werten,
 *         damit die Phase-2-Logik (BatteryManager.decideAction) ohne echte
 *         On-Chain-Daten getestet werden kann.
 * @dev Nur fuer Tests - nicht deployen.
 */
contract MockOracleStorage is IOracleStorage {
    mapping(address => MeterReading) private meter;
    mapping(address => BatteryState) private battery;
    mapping(address => bool) private registered;
    WeatherData private weather;
    uint256 private slot;

    function setMeter(address h, uint256 consumptionWh, uint256 productionWh) external {
        meter[h] = MeterReading(consumptionWh, productionWh, block.timestamp);
    }

    function setBattery(address h, uint256 socPercent, uint256 capacityWh, uint256 maxRateWh) external {
        battery[h] = BatteryState(socPercent, capacityWh, maxRateWh, block.timestamp);
    }

    function setWeather(uint256 irradianceWm2, int256 temperatureC, uint256 cloudCover) external {
        weather = WeatherData(irradianceWm2, temperatureC, cloudCover, block.timestamp);
    }

    function setRegistered(address h, bool v) external { registered[h] = v; }
    function setSlot(uint256 s) external { slot = s; }

    function getLatestMeterReading(address h) external view returns (MeterReading memory) { return meter[h]; }
    function getLatestBatteryState(address h) external view returns (BatteryState memory) { return battery[h]; }
    function getLatestWeather() external view returns (WeatherData memory) { return weather; }
    function getCurrentSlot() external view returns (uint256) { return slot; }
    function isHouseholdRegistered(address h) external view returns (bool) { return registered[h]; }
}
