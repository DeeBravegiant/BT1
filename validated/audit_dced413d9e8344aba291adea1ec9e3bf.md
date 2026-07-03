Audit Report

## Title
Missing Minimum Output Slippage Guard in L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The L2 pool deposit functions in `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolNoWrapper` accept no `minAmountOut` parameter. A user who previews the expected output via `viewSwapRsETHAmountAndFee` off-chain and then submits a deposit transaction can receive fewer wrsETH/rsETH tokens than expected if the oracle rate is updated between preview and execution. The mainnet `LRTDepositPool` already implements this guard via `minRSETHAmountExpected`, but the L2 pools do not.

## Finding Description
In `RSETHPoolV3.sol`, both `deposit(string memory referralId)` (ETH path, lines 246–265) and `deposit(address token, uint256 amount, string memory referralId)` (LST path, lines 271–293) compute the output amount at execution time by calling `viewSwapRsETHAmountAndFee`, which reads the live oracle rate via `getRate()` at lines 304–307:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Because `rsETHToETHrate` increases monotonically as EigenLayer rewards accrue, any oracle update between the moment a user previews the swap and the moment the transaction is mined will reduce the number of wrsETH tokens minted. There is no parameter the user can supply to bound the minimum acceptable output, and no on-chain check enforces a minimum.

The identical pattern is confirmed in `RSETHPoolV3ExternalBridge.sol` (lines 377, 405) and `RSETHPoolNoWrapper.sol` (lines 237, 264).

By contrast, `LRTDepositPool._beforeDeposit` (lines 667–669) enforces:
```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```
This protection is entirely absent from all three L2 pool contracts. No existing modifier or check in the L2 deposit paths compensates for this omission.

## Impact Explanation
A depositor on any supported L2 who calls `deposit()` after previewing the expected output via `viewSwapRsETHAmountAndFee` can receive fewer wrsETH/rsETH tokens than they agreed to at signing time. The tokens received are still backed by the same ETH value (the rate only rises), so no ETH value is lost. The contract fails to deliver the number of tokens the user expected at the time of signing.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
The rsETH oracle rate is updated periodically as EigenLayer rewards accrue; this is normal, expected protocol operation requiring no adversarial action. Any user whose transaction is delayed in the mempool (gas price competition, network congestion, slow block) is exposed. The condition is routinely triggered by normal protocol operation and requires no special capability from any party.

**Likelihood: Medium.**

## Recommendation
Add a `minRsETHAmountOut` parameter to all public `deposit` functions in `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolNoWrapper`, mirroring the pattern already used in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountOut)
    external payable nonReentrant whenNotPaused ...
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountOut) revert SlippageExceeded();
    ...
}
```

## Proof of Concept
1. Alice calls `RSETHPoolV3.viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees she will receive `950e15` wrsETH at the current oracle rate of `1.052e18`.
2. Alice submits `deposit{value: 1 ether}("ref")`.
3. Before Alice's transaction is mined, the rsETH oracle is updated (routine reward accrual), raising the rate to `1.060e18`.
4. Alice's transaction executes; `viewSwapRsETHAmountAndFee` now returns `943e15` wrsETH — ~0.7% fewer tokens than Alice expected.
5. Alice has no recourse; the contract mints the lower amount with no revert.

The mainnet path (`LRTDepositPool.depositETH`) would have reverted at step 4 if Alice had passed `minRSETHAmountExpected = 950e15`.

A Foundry fork test can reproduce this by: (a) forking at a block before an oracle update, (b) recording `viewSwapRsETHAmountAndFee` output, (c) rolling forward past an oracle update transaction, and (d) executing `deposit` and asserting the minted amount is less than the previewed amount with no revert.