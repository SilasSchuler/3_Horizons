// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../contracts/IncentiveController.sol";

/**
 * @notice Unit-Tests fuer Phase 3: IncentiveController Score-Modell + Preisfaktor.
 * @dev Laeuft mit `npx hardhat test solidity`. Das Test-Contract ist Deployer
 *      des IncentiveController und damit automatisch owner + authorizedAI,
 *      kann also submitForecast()/submitActual() direkt aufrufen.
 *
 *      Konstanten aus dem Contract: Start-Score 500, SCORE_REWARD 20,
 *      SCORE_PENALTY 30, GOOD_DEVIATION 100 (10%), BAD_DEVIATION 250 (25%),
 *      BASE_MULTIPLIER 1000, MAX_DISCOUNT/MAX_PENALTY 200.
 */
contract IncentiveControllerTest {
    IncentiveController ic;
    address constant HOUSE = address(0xBEEF);

    function setUp() public {
        ic = new IncentiveController();
    }

    function _round(address h, uint256 slot, uint256 fCons, uint256 fProd, uint256 aCons, uint256 aProd) internal {
        ic.submitForecast(h, slot, fCons, fProd);
        ic.submitActual(h, slot, aCons, aProd);
    }

    // Unbekannter Haushalt -> neutraler Multiplikator, Score 0
    function test_UnknownHousehold_IsNeutral() public {
        require(ic.getPriceMultiplier(address(0x1234)) == 1000, "expected neutral");
        require(ic.getReputationScore(address(0x1234)) == 0, "expected score 0");
    }

    // Forecast ohne Actual -> Score wird auf 500 initialisiert, Multiplikator neutral
    function test_ForecastInitialisesScore() public {
        ic.submitForecast(HOUSE, 1, 1000, 0);
        require(ic.getReputationScore(HOUSE) == 500, "expected init 500");
        require(ic.getPriceMultiplier(HOUSE) == 1000, "expected neutral");
    }

    // Sehr gute Prognose (<10% Abweichung) -> Score +20 -> leichter Rabatt
    function test_GoodForecast_RaisesScore_Discount() public {
        _round(HOUSE, 1, 1000, 0, 1050, 0);           // Abweichung 50 Promille
        require(ic.getReputationScore(HOUSE) == 520, "expected 520");
        require(ic.getPriceMultiplier(HOUSE) == 992, "expected 992");
    }

    // Schlechte Prognose (>25% Abweichung) -> Score -30 -> Aufschlag
    function test_BadForecast_LowersScore_Penalty() public {
        _round(HOUSE, 1, 1000, 0, 1400, 0);           // Abweichung 400 Promille
        require(ic.getReputationScore(HOUSE) == 470, "expected 470");
        require(ic.getPriceMultiplier(HOUSE) == 1012, "expected 1012");
    }

    // Mittlere Abweichung (10-25%) -> neutral, kein Score-Change
    function test_MediocreForecast_IsNeutral() public {
        _round(HOUSE, 1, 1000, 0, 1150, 0);           // Abweichung 150 Promille
        require(ic.getReputationScore(HOUSE) == 500, "expected unchanged 500");
        require(ic.getPriceMultiplier(HOUSE) == 1000, "expected neutral");
    }

    // Produktions-Abweichung fliesst mit 30% ein: cons perfekt, prod 100% daneben
    // -> gewichtete Abweichung = 0*0.7 + 1000*0.3 = 300 > 250 -> Penalty
    function test_ProductionDeviation_CountsWeighted() public {
        _round(HOUSE, 1, 1000, 1000, 1000, 2000);
        require(ic.getReputationScore(HOUSE) == 470, "expected penalty despite perfect consumption");
    }

    // Wiederholt gute Prognosen -> Score deckelt bei 1000 -> max. Rabatt 800
    function test_ScoreCapsAt1000_MaxDiscount() public {
        for (uint256 s = 1; s <= 30; s++) {
            _round(HOUSE, s, 1000, 0, 1000, 0);       // Abweichung 0
        }
        require(ic.getReputationScore(HOUSE) == 1000, "expected cap 1000");
        require(ic.getPriceMultiplier(HOUSE) == 800, "expected max discount 800");
    }

    // Wiederholt schlechte Prognosen -> Score-Floor bei 1 (nicht 0) -> bleibt bestraft
    function test_ScoreFloorsAt1_StaysPenalised() public {
        for (uint256 s = 1; s <= 25; s++) {
            _round(HOUSE, s, 1000, 0, 5000, 0);       // Abweichung 4000 Promille
        }
        require(ic.getReputationScore(HOUSE) == 1, "expected floor 1");
        require(ic.getPriceMultiplier(HOUSE) == 1199, "expected near-max penalty");
    }

    // onlyAI-Guard: nicht autorisierter Aufrufer wird abgewiesen
    function test_SubmitForecast_RevertsForNonAI() public {
        Outsider o = new Outsider(ic);
        try o.tryForecast() {
            require(false, "expected revert");
        } catch {}
    }
}

/// @dev Separates Contract = eigener msg.sender, nicht in authorizedAI.
contract Outsider {
    IncentiveController ic;
    constructor(IncentiveController _ic) { ic = _ic; }
    function tryForecast() external { ic.submitForecast(address(0xBEEF), 1, 1000, 0); }
}
