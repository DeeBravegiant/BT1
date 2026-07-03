Audit Report

## Title
Missing Minimum rsETH Output Protection in L2 Pool Deposit Functions - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

## Summary
All L2 pool `deposit` functions compute the rsETH output at execution time from the oracle rate but accept no `minRsETHAmount` floor, so any oracle rate increase between transaction submission and inclusion silently delivers fewer rsETH tokens than the user observed. The L1 `LRTDepositPool._beforeDeposit` already enforces this protection via `minRSETHAmountExpected`, making the L2 omission an inconsistency with a concrete, unrecoverable impact on depositors.

## Finding Description
Every L2 pool `deposit` entry-point follows the same pattern: call `viewSwapRsETHAmountAndFee`, accumulate the fee, and transfer/mint the computed `rsETHAmount` with no floor check.

`RSETHPoolV3ExternalBridge.sol` lines 366–384:
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // no minimum guard
```
The rate used is fetched live inside `viewSwapRsETHAmountAndFee` (line 423: `rsETHToETHrate = getRate()`), so any oracle update that lands before the deposit transaction is mined changes the output silently.

The identical pattern exists in:
- `RSETHPool.sol` lines 271–275 and 298–302
- `RSETHPoolNoWrapper.sol` lines 237–241 and 264–268
- `RSETHPoolV2ExternalBridge.sol` lines 294–298
- `RSETHPoolV3WithNativeChainBridge.sol` lines 294–298 and 322–326

By contrast, `LRTDepositPool._beforeDeposit` (lines 667–669) reverts when `rsethAmountToMint < minRSETHAmountExpected`. No equivalent guard exists in any L2 pool. There is no other check in the deposit flow that bounds the minimum output.

## Impact Explanation
A depositor who observes `rsETHToETHrate = 1.05e18` and submits a 1 ETH deposit expecting ≈ 0.952 rsETH will receive ≈ 0.909 rsETH if the oracle updates to `1.10e18` before inclusion. At the new rate, 0.909 rsETH is worth ≈ 1.00 ETH — the depositor loses the ~4.5% appreciation that accrued between observation and execution with no revert and no recourse. This matches the allowed impact **"Contract fails to deliver promised returns, but doesn't lose value"** at **Low** severity: the principal asset class (ETH) is exchanged rather than stolen, but the rsETH return is lower than the rate the user acted on.

## Likelihood Explanation
The rsETH/ETH oracle rate is updated periodically as staking rewards accrue; these updates are routine protocol operations, not attacker-controlled. No privileged access or external compromise is required — any oracle refresh that lands in the same block or between submission and inclusion triggers the condition. On active L2s with frequent oracle refreshes this is a realistic, low-probability event that any depositor can encounter without any mistake on their part.

## Recommendation
Add a `minRsETHAmount` parameter to all L2 pool `deposit` entry-points and revert when the computed amount falls below it, mirroring `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert MinimumAmountToReceiveNotMet();
    ...
}
```
Apply the same change to both ETH and token `deposit` overloads in all five pool contracts.

## Proof of Concept
1. Deploy (or fork) any L2 pool, e.g. `RSETHPoolV3ExternalBridge`.
2. Set oracle to return `rsETHToETHrate = 1.05e18`.
3. Call `viewSwapRsETHAmountAndFee(1e18)` — observe expected output ≈ `952380952380952380`.
4. Before mining the deposit, update the oracle to `rsETHToETHrate = 1.10e18`.
5. Call `deposit{value: 1e18}("ref")` — `wrsETH.mint` executes with ≈ `909090909090909090` rsETH.
6. Assert `actualRsETH < expectedRsETH` with no revert — confirms silent shortfall.

Foundry fuzz test: fuzz `rsETHToETHrate` over `[1e18, 2e18]`, record `viewSwapRsETHAmountAndFee` output before and after the rate change, call `deposit`, and assert the minted amount equals the post-update (lower) value rather than the pre-update (higher) value, with no revert path available to the user.