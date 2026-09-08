// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IBatteryManager
 * @notice Interface für die optionale Phase-2-Integration in P2PEnergyMarket.
 * @dev BatteryManager.sol implementiert dieses Interface (siehe dort).
 *      P2PEnergyMarket importiert nur dieses Interface, nicht den vollen
 *      BatteryManager-Code - dadurch bleibt die Abhängigkeit einseitig.
 */
interface IBatteryManager {
    enum Action { IDLE, CHARGE, DISCHARGE }

    /// @notice Trifft/protokolliert die Lade-/Entladeentscheidung für einen Haushalt.
    /// @dev Nicht `view`: BatteryManager schreibt die Entscheidung in den
    ///      eigenen Storage und emittiert ein Event.
    function decideAction(address household) external returns (Action, uint256);

    /// @notice Prüft, ob ein Haushalt überhaupt eine verwaltete Batterie hat.
    /// @dev Wichtig für Aufrufer: nicht jeder Haushalt (z.B. reine Konsumenten
    ///      ohne PV/Batterie) ist zwingend in BatteryManager registriert.
    function isManaged(address household) external view returns (bool);
}