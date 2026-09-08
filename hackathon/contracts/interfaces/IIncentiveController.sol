// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IIncentiveController
 * @notice Interface für die optionale Phase-3-Integration in P2PEnergyMarket.
 * @dev IncentiveController.sol implementiert dieses Interface (siehe dort).
 *      P2PEnergyMarket importiert nur dieses Interface, nicht den vollen
 *      IncentiveController-Code - dadurch bleibt die Abhängigkeit einseitig.
 */
interface IIncentiveController {
    /// @notice Gibt den Preismultiplikator für einen Haushalt zurück.
    /// @dev `view`, da nur Reputationsscore gelesen wird (kein State-Change,
    ///      im Gegensatz zu IBatteryManager.decideAction()).
    /// @return multiplier in Promille (1000 = neutral, <1000 = Rabatt, >1000 = Aufschlag)
    function getPriceMultiplier(address household) external view returns (uint256 multiplier);
}