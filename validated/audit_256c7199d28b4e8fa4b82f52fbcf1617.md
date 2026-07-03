Audit Report

## Title
Permissionless `FeeReceiver.sendFunds()` + `LRTOracle.updateRSETHPrice()` Enable Yield Frontrunning - (File: `contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

## Summary

`FeeReceiver.sendFunds()` has no access control and can be called by any external account to flush accumulated MEV/EL rewards into `LRTDepositPool`, immediately increasing the protocol's ETH TVL. `LRTOracle.updateRSETHPrice()` is similarly callable by anyone. An attacker can atomically deposit at the stale (pre-reward) rsETH price, trigger the reward flush and price update, then initiate a withdrawal at the inflated price, locking in a profit extracted from existing stakers' yield.

## Finding Description

**Root Cause 1 — `FeeReceiver.sendFunds()` has no access control:**

`sendFunds()` is declared `external` with no role modifier. Any EOA or contract can call it at any time, immediately flushing the entire ETH balance of `FeeReceiver` into `LRTDepositPool` via `receiveFromRewardReceiver()`.

```solidity
// contracts/FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

This increases `address(LRTDepositPool).balance`, which feeds directly into `getETHDistributionData()` → `getTotalAssetDeposits(ETH_TOKEN)` → `_getTotalEthInProtocol()`.

**Root Cause 2 — `LRTOracle.updateRSETHPrice()` is permissionless:**

`updateRSETHPrice()` is `public whenNotPaused` with no role restriction. Any caller can trigger a price recalculation at any time.

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

A `pricePercentageLimit` guard exists at lines 252–266 that reverts non-manager callers if the price increase exceeds the configured threshold. However, this guard is only active when `pricePercentageLimit > 0`. When `pricePercentageLimit == 0` (the default unset state), the guard is entirely bypassed (`isPriceIncreaseOffLimit = pricePercentageLimit > 0 && ...` evaluates to `false`), allowing any caller to update the price regardless of the magnitude of the increase.

**Root Cause 3 — Deposit uses stale stored `rsETHPrice`:**

`getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()`, which is the **last stored** price, not a live calculation. A deposit made before `updateRSETHPrice()` is called uses the old (lower) price, minting more rsETH per ETH than the post-reward rate.

```solidity
// contracts/LRTDepositPool.sol L519-521
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**Root Cause 4 — `initiateWithdrawal()` locks in the current (post-update) price:**

`initiateWithdrawal()` calls `getExpectedAssetAmount()` which reads `lrtOracle.rsETHPrice()` at that moment and stores it as `expectedAssetAmount` in the withdrawal request. If the attacker initiates withdrawal after the price update, the higher price is locked in.

```solidity
// contracts/LRTWithdrawalManager.sol L168-173
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

```solidity
// contracts/LRTWithdrawalManager.sol L590-594
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**Why existing guards are insufficient:**

The `_calculatePayoutAmount` function in `unlockQueue()` takes the minimum of `request.expectedAssetAmount` and the current calculated return at unlock time. Since rewards only accumulate over time (barring slashing), the price at `unlockQueue()` time will typically be ≥ P2, so the attacker receives the full `expectedAssetAmount` locked at the inflated price.

The `_checkAndUpdateDailyFeeMintLimit` guard only limits fee minting; it does not prevent the price from being updated. If `protocolFeeInBPS == 0`, no fee is minted and the check passes trivially.

The `getAvailableAssetAmount` check in `initiateWithdrawal()` is satisfied because `sendFunds()` increases `getTotalAssetDeposits(ETH)` by the reward amount, making the inflated `expectedAssetAmount` available.

## Impact Explanation

**High — Theft of unclaimed yield.**

The attacker deposits at price P₁ (pre-reward), receives `X / P₁` rsETH. After the reward flush raises the price to P₂ > P₁, the attacker initiates withdrawal and locks in `(X / P₁) × P₂` ETH. The profit `X × (P₂/P₁ − 1)` is extracted directly from the yield that should have accrued to existing rsETH holders. This is a concrete, repeatable theft of yield from protocol participants, matching the "Theft of unclaimed yield" impact class.

## Likelihood Explanation

- `FeeReceiver.sendFunds()` is called regularly as MEV/EL rewards accumulate; the attacker can also call it themselves at any time.
- `updateRSETHPrice()` is permissionless and can be called by the attacker in the same transaction or block.
- The attack requires no special privileges, no oracle manipulation, and no external protocol compromise.
- The 8-day withdrawal delay (`withdrawalDelayBlocks = 8 days / 12 seconds`) is the only friction, but the profit is locked in at `initiateWithdrawal()` time, so the attacker bears no price risk after step 3.
- The attack is blocked when `pricePercentageLimit > 0` and the reward flush causes a price increase exceeding the limit. However, when `pricePercentageLimit == 0` (unset), or when accumulated rewards are small enough to stay within the limit, the attack succeeds unconditionally.
- **Likelihood: Medium** — requires monitoring the mempool or self-triggering `sendFunds()`; profitable only when reward accumulation is large enough to offset gas and the 8-day capital lockup cost.

## Recommendation

1. **Add access control to `FeeReceiver.sendFunds()`**: Restrict it to `MANAGER` or `OPERATOR` role so the timing of reward injection is controlled.
2. **Alternatively, update rsETH price atomically inside `sendFunds()`**: Call `updateRSETHPrice()` as the first action before transferring ETH, so the price is already updated before any deposit in the same block can benefit.
3. **Ensure `pricePercentageLimit` is always set to a non-zero value**: This limits the magnitude of single-block price jumps that unprivileged callers can trigger, reducing the per-attack profit.
4. **Consider a deposit fee or a time-weighted price**: Prevent single-block arbitrage by smoothing the price update or charging a small entry fee that exceeds the expected single-block reward delta.

## Proof of Concept

```
Block N (attacker's bundle):
  tx1: attacker calls depositETH{value: 1000 ETH}(0, "")
       → rsETHPrice = P1 (stale, pre-reward)
       → attacker receives 1000e18 / P1 rsETH

  tx2: attacker calls FeeReceiver.sendFunds()
       → 50 ETH MEV rewards flow into LRTDepositPool
       → LRTDepositPool.balance increases by 50 ETH
       → getTotalAssetDeposits(ETH) increases by 50 ETH

  tx3: attacker calls LRTOracle.updateRSETHPrice()
       → totalETHInProtocol increases by 50 ETH
       → rsETHPrice = P2 > P1
       (succeeds when pricePercentageLimit == 0 or increase is within limit)

  tx4: attacker calls LRTWithdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHBalance, "")
       → expectedAssetAmount = rsETHBalance * P2 / 1e18 = (1000e18/P1) * P2 / 1e18 > 1000 ETH
       → locked at the higher price; getAvailableAssetAmount check passes
         because sendFunds() increased total ETH in protocol

Block N + 57,600 (after 8-day delay):
  Operator calls unlockQueue() → _calculatePayoutAmount returns min(expectedAssetAmount, currentReturn)
  Since price at unlock time >= P2 (rewards accumulate), payout = expectedAssetAmount

  tx5: attacker calls completeWithdrawal(ETH_TOKEN, "")
       → receives (1000e18 / P1) * P2 ETH > 1000 ETH
       → profit ≈ 1000 ETH * (P2/P1 - 1) ≈ 50 * (1000/totalTVL) ETH
         stolen from existing stakers' yield
```

**Foundry fork test plan**: Fork mainnet, impersonate attacker EOA, call `depositETH`, `FeeReceiver.sendFunds()`, `LRTOracle.updateRSETHPrice()`, `initiateWithdrawal()` in sequence. Roll forward 57,600 blocks. Have operator call `unlockQueue()`. Call `completeWithdrawal()`. Assert attacker ETH balance > initial deposit.