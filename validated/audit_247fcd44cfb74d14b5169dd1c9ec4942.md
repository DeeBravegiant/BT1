Audit Report

## Title
Missing Access Control on `sendFunds()` Enables Front-Running of MEV Reward Distribution - (File: contracts/FeeReceiver.sol)

## Summary

`FeeReceiver.sendFunds()` has no access control modifier, allowing any external caller to flush accumulated MEV/execution-layer ETH rewards into `LRTDepositPool` at an arbitrary time. Combined with the permissionless `LRTOracle.updateRSETHPrice()`, an attacker can sandwich the reward distribution — depositing before and withdrawing after — to capture a disproportionate share of accumulated yield that rightfully belongs to existing rsETH holders.

## Finding Description

`FeeReceiver.sendFunds()` is declared `external` with no role check:

```solidity
// contracts/FeeReceiver.sol, L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

The target `receiveFromRewardReceiver()` is equally unguarded:

```solidity
// contracts/LRTDepositPool.sol, L61
function receiveFromRewardReceiver() external payable { }
```

Once ETH lands in `LRTDepositPool`, it is immediately included in `_getTotalEthInProtocol()` via `getETHDistributionData()` → `address(this).balance` (L480), which drives `_updateRsETHPrice()`. The price update function is itself permissionless:

```solidity
// contracts/LRTOracle.sol, L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

The `pricePercentageLimit` guard at L252–266 only blocks `updateRSETHPrice()` when the price increase exceeds the configured threshold **and** the caller is not a manager. When `pricePercentageLimit == 0` (disabled) or when accumulated rewards are small enough to stay within the threshold, the guard provides no protection. An attacker can monitor on-chain accumulation and time the attack to stay within the limit.

The complete exploit path:
1. Observe `FeeReceiver` has accumulated `R` ETH (publicly visible on-chain).
2. Call `LRTDepositPool.depositETH{value: D}()` at the current price `P` → receive `D/P` rsETH.
3. Call `FeeReceiver.sendFunds()` → `R` ETH moves to `LRTDepositPool`, inflating TVL.
4. Call `LRTOracle.updateRSETHPrice()` → new price = `(TVL + D + R) / totalRsETH`.
5. Initiate withdrawal at the new higher price, locking in the inflated exchange rate.
6. After `withdrawalDelayBlocks`, claim → receive `D + R·D/(TVL+D)` ETH.
7. Net profit = `R·D/(TVL+D)` ETH, extracted from existing holders' unclaimed yield.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders accumulate MEV rewards over time in `FeeReceiver`. The protocol design intends for a privileged manager to control when those rewards are flushed and the price updated, so that the timing cannot be exploited. The missing access control breaks this invariant: an attacker who deposits just before triggering `sendFunds()` dilutes the reward share of all pre-existing holders. In the concrete example (100 ETH TVL, 10 ETH accumulated rewards, attacker deposits 100 ETH), existing holders receive only 5 ETH of the 10 ETH reward instead of the full 10 ETH — the other 5 ETH is captured by the attacker. This is direct, quantifiable theft of unclaimed yield from legitimate protocol participants, matching the allowed High impact class.

## Likelihood Explanation

**Medium.** The attack requires no special role or privileged access — only the ability to call public contract functions and hold ETH. The `FeeReceiver` balance is publicly visible on-chain, making it straightforward to monitor and time the attack. The only friction is the `withdrawalDelayBlocks` (~8 days) before the attacker can claim, which introduces capital lock-up cost but does not prevent the attack. The attack is repeatable every reward accumulation cycle.

## Recommendation

Add an access control modifier to `sendFunds()` restricting it to a trusted role:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

This ensures only the protocol manager can control the timing of reward distribution, eliminating the front-running vector. The `FeeReceiver` contract already imports `AccessControlUpgradeable` and grants `LRTConstants.MANAGER` during `initialize()`, so the modifier is immediately available.

## Proof of Concept

Minimal Foundry fork test sequence:

```solidity
// 1. Setup: existing holders have deposited, FeeReceiver has accumulated R ETH
uint256 R = address(feeReceiver).balance; // e.g. 10 ether

// 2. Attacker deposits D ETH at current price P
uint256 D = 100 ether;
vm.prank(attacker);
lrtDepositPool.depositETH{value: D}(0, "");
uint256 rsethReceived = rsETH.balanceOf(attacker); // D / P

// 3. Attacker flushes rewards (no access control)
vm.prank(attacker);
feeReceiver.sendFunds(); // R ETH moves to depositPool

// 4. Attacker triggers price update (permissionless)
vm.prank(attacker);
lrtOracle.updateRSETHPrice(); // price increases to (TVL+D+R)/totalRsETH

// 5. Attacker initiates withdrawal at inflated price
vm.prank(attacker);
lrtWithdrawalManager.initiateWithdrawal(rsethReceived, ...);

// 6. Fast-forward past withdrawalDelayBlocks, claim
vm.roll(block.number + withdrawalDelayBlocks + 1);
vm.prank(attacker);
lrtWithdrawalManager.claim(...);

// Assert: attacker received > D ETH, profit = R*D/(TVL+D)
assertGt(attacker.balance, D);
```