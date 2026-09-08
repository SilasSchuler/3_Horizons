// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IOracleStorage.sol";

/**
 * @title OracleStorage
 * @notice Zentraler On-Chain Speicher für alle Mess- und Wetterdaten.
 * @dev VOLLSTÄNDIG VORGEGEBEN - Teams modifizieren diesen Contract NICHT.
 *
 *      Schreibzugriff: Nur autorisierte Oracle-Adressen (gesetzt vom Owner).
 *      Lesezugriff: Öffentlich.
 *
 *      Das Python-Skript `oracle_writer.py` schreibt jeden Slot neue Werte.
 *      Andere Contracts (P2PEnergyMarket, BatteryManager, etc.) lesen via Interface.
 */
contract OracleStorage is IOracleStorage {

    // ─────────────────────────────────────────────────────────────
    //  Storage
    // ─────────────────────────────────────────────────────────────

    address public owner;
    mapping(address => bool) public authorizedOracles;
    mapping(address => bool) public registeredHouseholds;

    // Aktuellster Wert pro Haushalt (für einfachen Zugriff)
    mapping(address => MeterReading) private latestMeter;
    mapping(address => BatteryState) private latestBattery;
    WeatherData private latestWeather;

    // Historie pro Haushalt und Slot (optional, für AI-Phase 3)
    mapping(address => mapping(uint256 => MeterReading)) public meterHistory;

    // Slot-Counter: 1 Slot = 1 reale Minute = 15 Simulationsminuten
    // (konsistent mit config.json und data_simulator.py)
    uint256 public currentSlot;
    uint256 public immutable startTimestamp;
    uint256 public constant SLOT_DURATION = 60; // 60 Sekunden

    // ─────────────────────────────────────────────────────────────
    //  Events
    // ─────────────────────────────────────────────────────────────

    event HouseholdRegistered(address indexed household);
    event OracleAuthorized(address indexed oracle);
    event OracleRevoked(address indexed oracle);
    event MeterUpdated(address indexed household, uint256 slot, uint256 consumption, uint256 production);
    event BatteryUpdated(address indexed household, uint256 slot, uint256 soc);
    event WeatherUpdated(uint256 slot, uint256 irradiance, int256 temperature);

    // ─────────────────────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    modifier onlyOracle() {
        require(authorizedOracles[msg.sender], "Not authorized oracle");
        _;
    }

    // ─────────────────────────────────────────────────────────────
    //  Constructor
    // ─────────────────────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
        authorizedOracles[msg.sender] = true; // Deployer ist initial Oracle
        startTimestamp = block.timestamp;
        emit OracleAuthorized(msg.sender);
    }

    // ─────────────────────────────────────────────────────────────
    //  Admin
    // ─────────────────────────────────────────────────────────────

    function authorizeOracle(address oracle) external onlyOwner {
        authorizedOracles[oracle] = true;
        emit OracleAuthorized(oracle);
    }

    function revokeOracle(address oracle) external onlyOwner {
        authorizedOracles[oracle] = false;
        emit OracleRevoked(oracle);
    }

    function registerHousehold(address household) external onlyOracle {
        registeredHouseholds[household] = true;
        emit HouseholdRegistered(household);
    }

    // ─────────────────────────────────────────────────────────────
    //  Schreibfunktionen (nur Oracle)
    // ─────────────────────────────────────────────────────────────

    function updateSlot() external onlyOracle {
        currentSlot = (block.timestamp - startTimestamp) / SLOT_DURATION;
    }

    function updateMeter(
        address household,
        uint256 consumptionWh,
        uint256 productionWh
    ) external onlyOracle {
        require(registeredHouseholds[household], "Not registered");
        MeterReading memory r = MeterReading({
            consumptionWh: consumptionWh,
            productionWh: productionWh,
            timestamp: block.timestamp
        });
        latestMeter[household] = r;
        meterHistory[household][currentSlot] = r;
        emit MeterUpdated(household, currentSlot, consumptionWh, productionWh);
    }

    function updateBattery(
        address household,
        uint256 socPercent,
        uint256 capacityWh,
        uint256 maxRateWh
    ) external onlyOracle {
        require(registeredHouseholds[household], "Not registered");
        require(socPercent <= 100, "Invalid SoC");
        latestBattery[household] = BatteryState({
            socPercent: socPercent,
            capacityWh: capacityWh,
            maxRateWh: maxRateWh,
            timestamp: block.timestamp
        });
        emit BatteryUpdated(household, currentSlot, socPercent);
    }

    function updateWeather(
        uint256 irradianceWm2,
        int256 temperatureC,
        uint256 cloudCover
    ) external onlyOracle {
        require(cloudCover <= 100, "Invalid cloud cover");
        latestWeather = WeatherData({
            irradianceWm2: irradianceWm2,
            temperatureC: temperatureC,
            cloudCover: cloudCover,
            timestamp: block.timestamp
        });
        emit WeatherUpdated(currentSlot, irradianceWm2, temperatureC);
    }

    // ─────────────────────────────────────────────────────────────
    //  Getter
    // ─────────────────────────────────────────────────────────────

    function getLatestMeterReading(address household) external view returns (MeterReading memory) {
        return latestMeter[household];
    }

    function getLatestBatteryState(address household) external view returns (BatteryState memory) {
        return latestBattery[household];
    }

    function getLatestWeather() external view returns (WeatherData memory) {
        return latestWeather;
    }

    function getCurrentSlot() external view returns (uint256) {
        return currentSlot;
    }

    function isHouseholdRegistered(address household) external view returns (bool) {
        return registeredHouseholds[household];
    }

    function getMeterAtSlot(address household, uint256 slot) external view returns (MeterReading memory) {
        return meterHistory[household][slot];
    }
}
