// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IEnergyStablecoin
 * @notice Interface des extern bereitgestellten ERC-20 Stablecoins.
 * @dev Token nutzt 6 Decimals (USDT-Pattern).
 *      Adresse wird vom Organisator zu Beginn des Hackathons mitgeteilt.
 */
interface IEnergyStablecoin {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function allowance(address owner, address spender) external view returns (uint256);
    function decimals() external view returns (uint8);

    /// @notice Self-service Faucet: Jede Adresse kann sich Tokens für Tests holen.
    function faucet() external;
}
