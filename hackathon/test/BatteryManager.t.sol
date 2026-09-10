// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../contracts/BatteryManager.sol";
import "../contracts/mocks/MockOracleStorage.sol";

/**
 * @notice Unit-Tests fuer Phase 2: BatteryManager.decideAction().
 * @dev Laeuft mit `forge test` (Foundry). Keine forge-std noetig -
 *      `setUp()` und `test*`-Funktionen werden automatisch erkannt.
 *
 *      Annahmen in allen Faellen: capacity = 10_000 Wh, maxRate = 2_000 Wh.
 *      Schwellen aus dem Contract: MAX_CHARGE_SOC=90, MIN_DISCHARGE_SOC=20,
 *      MIN_DISCHARGE_SOC_CLOUDY=40, CLOUD_COVER_THRESHOLD=80.
 */
contract BatteryManagerTest {
    MockOracleStorage oracle;
    BatteryManager bm;
    address constant HOUSE = address(0xBEEF);

    function setUp() public {
        oracle = new MockOracleStorage();
        oracle.setRegistered(HOUSE, true);
        oracle.setSlot(1);
        bm = new BatteryManager(address(oracle));
        bm.addHousehold(HOUSE);
    }

    // Ueberschuss + SoC < 90  -> CHARGE, gedeckelt auf maxRate
    function test_Surplus_Charges_CappedAtMaxRate() public {
        oracle.setBattery(HOUSE, 50, 10_000, 2_000);
        oracle.setMeter(HOUSE, 1_000, 4_000);        // Ueberschuss 3_000
        oracle.setWeather(500, 200, 10);
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.CHARGE, "expected CHARGE");
        require(amt == 2_000, "expected cap at maxRate");
    }

    // Ueberschuss aber SoC >= 90 -> IDLE
    function test_Surplus_ButFull_Idles() public {
        oracle.setBattery(HOUSE, 95, 10_000, 2_000);
        oracle.setMeter(HOUSE, 1_000, 4_000);
        oracle.setWeather(500, 200, 10);
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.IDLE, "expected IDLE");
        require(amt == 0, "expected 0");
    }

    // Defizit + SoC > 20 (nicht bewoelkt) -> DISCHARGE
    function test_Deficit_Discharges() public {
        oracle.setBattery(HOUSE, 50, 10_000, 2_000);
        oracle.setMeter(HOUSE, 4_000, 1_000);        // Defizit 3_000
        oracle.setWeather(500, 200, 10);
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.DISCHARGE, "expected DISCHARGE");
        require(amt == 2_000, "expected cap at maxRate");
    }

    // Defizit + bewoelkt (>80%) + SoC 30 < strengere Reserve 40 -> IDLE
    function test_Deficit_Cloudy_BelowStricterReserve_Idles() public {
        oracle.setBattery(HOUSE, 30, 10_000, 2_000);
        oracle.setMeter(HOUSE, 4_000, 1_000);
        oracle.setWeather(100, 150, 90);             // cloudCover 90 > 80
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.IDLE, "expected IDLE (cloudy reserve)");
        require(amt == 0, "expected 0");
    }

    // Defizit + bewoelkt + SoC 50 > 40 -> DISCHARGE, begrenzt durch Reserve-Kapazitaet
    function test_Deficit_Cloudy_AboveReserve_DischargesLimited() public {
        oracle.setBattery(HOUSE, 50, 10_000, 2_000);
        oracle.setMeter(HOUSE, 4_000, 1_000);        // Defizit 3_000
        oracle.setWeather(100, 150, 90);
        // verfuegbar = 10_000 * (50-40) / 100 = 1_000
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.DISCHARGE, "expected DISCHARGE");
        require(amt == 1_000, "expected limit by cloudy reserve capacity");
    }

    // Produktion == Verbrauch -> IDLE
    function test_Balanced_Idles() public {
        oracle.setBattery(HOUSE, 50, 10_000, 2_000);
        oracle.setMeter(HOUSE, 2_000, 2_000);
        oracle.setWeather(500, 200, 10);
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.IDLE, "expected IDLE");
        require(amt == 0, "expected 0");
    }

    // Keine Batterie (capacity 0) -> IDLE
    function test_NoBattery_Idles() public {
        oracle.setBattery(HOUSE, 0, 0, 0);
        oracle.setMeter(HOUSE, 1_000, 4_000);
        oracle.setWeather(500, 200, 10);
        (IBatteryManager.Action a, uint256 amt) = bm.decideAction(HOUSE);
        require(a == IBatteryManager.Action.IDLE, "expected IDLE");
        require(amt == 0, "expected 0");
    }
}
