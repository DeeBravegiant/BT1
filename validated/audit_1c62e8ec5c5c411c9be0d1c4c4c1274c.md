Audit Report

## Title
Missing Minimum Output Amount (Slippage Protection) in L2 Pool `deposit` Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

## Summary
All four L2 pool `deposit` entry points compute the rsETH output at execution time from a live oracle rate via `viewSwapRsETHAmountAndFee`, but accept no caller-supplied minimum output parameter. If the oracle rate advances between transaction submission and inclusion — through a routine protocol update — the depositor silently receives fewer rsETH than previewed with no on-chain recourse. The L1 `LRTDepositPool` already enforces this guard via `minRSETHAmountExpected`, making the omission a concrete inconsistency with a user-facing impact matching the allowed Low impact class.

## Finding Description
Every L2 pool `deposit` function delegates rate computation to `viewSwapRsETHAmountAndFee`, which reads `getRate()` from the live oracle at execution time:

**`RSETHPoolV3.sol` ETH path (L246–265):**
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
// ...
wrsETH.mint(msg.sender, rsETHAmount);   // no minAmountOut check
```

**`RSETHPoolV3.sol` token path (L271–293):**
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
// ...
wrsETH.mint(msg.sender, rsETHAmount);   // no minAmountOut check
```

The same pattern is present in `RSETHPool.sol` (L265–278, L284–305), `RSETHPoolNoWrapper.sol` (L231–244, L250–271), and `RSETHPoolV3ExternalBridge.sol` (L366–384, L390–412). In all cases `viewSwapRsETHAmountAndFee` computes `rsETHAmount = amountAfterFee * 1e18 / getRate()`, where `getRate()` is the live oracle value at the moment of execution.

By contrast, `LRTDepositPool.depositETH` and `depositAsset` both accept a `minRSETHAmountExpected` parameter and enforce it inside `_beforeDeposit` (L665–669):
```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

No equivalent guard exists in any of the four L2 pool contracts. A user who calls `viewSwapRsETHAmountAndFee` off-chain to preview their output and then submits a deposit has no mechanism to express an acceptable minimum on-chain. If the oracle rate increases before their transaction is included (a normal, recurring protocol event as rewards accrue), the computed `rsETHAmount` decreases and the transaction succeeds silently with a lower output.

## Impact Explanation
The depositor's ETH or LST is consumed in full; the rsETH received is priced at the updated oracle rate, so no absolute ETH value is lost. However, the contract fails to deliver the rsETH amount it implicitly promised when the user previewed the rate. This matches exactly the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value."**

## Likelihood Explanation
The rsETH oracle rate is updated periodically by the protocol as staking rewards accrue. On L2 chains with public mempools (Arbitrum, Optimism, Base), any pending deposit is visible. A routine oracle update landing in the same block as a deposit — without any attacker involvement — is sufficient to trigger the shortfall. No privileged access, no oracle manipulation, and no attacker profit is required; the condition arises from normal protocol operation and is repeatable on every oracle update cycle.

## Recommendation
Add a `minRsETHAmountOut` parameter to all eight affected `deposit` function signatures (ETH and token variants across all four contracts) and revert if the computed amount falls below it, mirroring `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountOut)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    // ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountOut) revert SlippageExceeded();
    // ...
}
```

Apply the same change to the token-deposit overload in `RSETHPoolV3`, `RSETHPool`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge`.

## Proof of Concept
1. Alice calls `viewSwapRsETHAmountAndFee(10 ether)` off-chain when `getRate()` = `1.05e18`; she expects ≈ 9.52 rsETH.
2. Alice submits `RSETHPoolV3.deposit{value: 10 ether}("ref")`.
3. Before Alice's transaction is mined, the protocol oracle is updated to `1.10e18` (routine reward accrual).
4. Alice's transaction executes: `rsETHAmount = 10e18 * 1e18 / 1.10e18 ≈ 9.09 rsETH`.
5. Alice receives ≈ 0.43 rsETH less than previewed; the transaction succeeds with no revert and no recourse.
6. Because no `minRsETHAmountOut` parameter exists, Alice had no way to express her acceptable slippage on-chain.

**Foundry fork test plan:** Fork an L2 deployment; call `viewSwapRsETHAmountAndFee` to record `expectedAmount`; simulate an oracle rate increase via `vm.mockCall` on `IOracle(rsETHOracle).getRate()`; call `deposit`; assert `rsETHAmount < expectedAmount` and that no revert occurred. Confirm the same test reverts when the L1 `LRTDepositPool.depositETH` is called with `minRSETHAmountExpected = expectedAmount`.