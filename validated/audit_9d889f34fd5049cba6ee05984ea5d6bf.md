Audit Report

## Title
Missing Minimum Output Amount (Slippage Protection) in L2 Pool `deposit` Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

## Summary
All four L2 pool contracts expose `deposit` functions that compute the rsETH output at execution time from a live oracle rate via `viewSwapRsETHAmountAndFee`, but accept no `minAmountOut` parameter. If the oracle rate increases between a user's off-chain preview and on-chain execution — due to a routine protocol reward accrual update — the user silently receives fewer rsETH than anticipated with no revert path. The L1 `LRTDepositPool` already enforces this guard, making the omission a concrete, confirmed inconsistency with a user-facing impact.

## Finding Description
Every L2 pool `deposit` function follows the same pattern: compute `rsETHAmount` from the live oracle rate, then transfer/mint that amount to the caller with no floor check.

**`RSETHPoolV3.sol` ETH path** (lines 246–265): `viewSwapRsETHAmountAndFee(amount)` is called, and `wrsETH.mint(msg.sender, rsETHAmount)` executes unconditionally.

**`RSETHPoolV3.sol` token path** (lines 271–293): identical pattern with `viewSwapRsETHAmountAndFee(amount, token)`.

**`RSETHPool.sol`** (lines 265–278, 284–305): same pattern, using `safeTransfer` instead of `mint`.

**`RSETHPoolNoWrapper.sol`** (lines 231–244, 250–271): same pattern, transferring raw rsETH.

**`RSETHPoolV3ExternalBridge.sol`** (lines 366–384, 390–412): same pattern.

The rate is sourced from `getRate()` inside `viewSwapRsETHAmountAndFee` (RSETHPoolV3.sol lines 299–308), which reads the live oracle at execution time.

By contrast, `LRTDepositPool.depositETH` and `depositAsset` (lines 76–118) both accept `minRSETHAmountExpected` and enforce it in `_beforeDeposit` (lines 665–669):
```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```
No equivalent guard exists in any of the four L2 pool contracts. A depositor has no on-chain mechanism to express acceptable slippage.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previews the expected rsETH output via `viewSwapRsETHAmountAndFee` and submits a transaction may receive materially fewer rsETH if the oracle rate increases before inclusion. The depositor's ETH or LST is consumed in full; the shortfall in rsETH tokens is permanent. Because the rsETH received is still priced at the updated oracle rate, the user does not lose absolute value — but the contract fails to deliver the token quantity the user was promised at submission time, matching the Low impact class exactly.

## Likelihood Explanation
The rsETH oracle rate is updated periodically as protocol rewards accrue. On all targeted L2 chains (Arbitrum, Optimism, Base, etc.) with public mempools, any pending deposit transaction is visible. Even without active MEV, a user whose transaction sits in the mempool during a routine oracle update will silently receive fewer rsETH. This is a realistic, recurring condition triggered by any normal depositor through the public `deposit` function — no privileged access or attacker capability required.

## Recommendation
Add a `minRsETHAmountOut` parameter to all eight affected `deposit` function signatures (ETH and token variants across all four contracts) and revert if the computed amount falls below it, mirroring `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountOut)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountOut) revert SlippageExceeded();
    ...
}
```

Apply the same change to the token-deposit overload in `RSETHPoolV3`, `RSETHPool`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge`.

## Proof of Concept
1. Alice calls `viewSwapRsETHAmountAndFee(10 ether)` off-chain when `getRate()` returns `1.05e18`. She expects ≈ 9.52 rsETH.
2. Alice submits `RSETHPoolV3.deposit{value: 10 ether}("ref")`.
3. Before Alice's transaction is mined, the protocol oracle is updated to `1.10e18` (routine reward accrual).
4. Alice's transaction executes: `rsETHAmount = 10e18 * 1e18 / 1.10e18 ≈ 9.09 rsETH`.
5. Alice receives ≈ 0.43 rsETH less than previewed, with no revert and no recourse.
6. Because no `minRsETHAmountOut` parameter exists, Alice had no way to express her acceptable slippage on-chain.

**Foundry fork test plan:** Fork the target L2, deploy the pool, call `viewSwapRsETHAmountAndFee` to record `expectedAmount`, prank the oracle updater to increase the rate, then call `deposit` and assert `rsETHAmount < expectedAmount` with no revert — confirming the absence of any slippage guard.