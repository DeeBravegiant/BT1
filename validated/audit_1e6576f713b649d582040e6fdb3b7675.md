Audit Report

## Title
Missing Slippage Protection in L2 Pool `deposit()` Functions Allows Users to Receive Fewer rsETH Than Expected - (File: contracts/pools/RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3ExternalBridge.sol)

## Summary
All four L2 pool contracts compute the rsETH output at execution time from a live oracle rate but accept no `minRsETHAmount` parameter in any `deposit()` overload. A user who previews the exchange rate off-chain and submits a deposit may receive materially fewer rsETH than observed at submission time, with no on-chain protection and no revert. The L1 `LRTDepositPool` already enforces an equivalent guard, making this an inconsistency with a concrete, reproducible impact.

## Finding Description
Every L2 pool `deposit()` function delegates output calculation to `viewSwapRsETHAmountAndFee`, which reads the live oracle rate via `getRate()` at execution time:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

This pattern appears identically in `RSETHPool.sol` (lines 311–319), `RSETHPoolNoWrapper.sol` (lines 277–285), `RSETHPoolV2ExternalBridge.sol` (lines 307–315), and `RSETHPoolV3ExternalBridge.sol` (lines 418–426). None of the six `deposit()` entry points across these four contracts accept or enforce a minimum rsETH output.

The oracle (`InterimRSETHOracle`) is updated by a `MANAGER_ROLE` holder via `setRate()`. Because rsETH is a yield-bearing token, the rate is monotonically increasing. Any rate update that occurs between a user's off-chain preview and on-chain execution reduces the rsETH minted for the same ETH input. The contract proceeds silently, transferring or minting the reduced amount with no revert.

By contrast, `LRTDepositPool.depositETH` and `LRTDepositPool.depositAsset` both accept `minRSETHAmountExpected` and enforce it in `_beforeDeposit`:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The L2 pools have no equivalent check.

## Impact Explanation
**Low – contract fails to deliver promised returns, but doesn't lose value.**

A user who calls `viewSwapRsETHAmountAndFee` off-chain to preview their expected rsETH output and then submits a deposit may receive fewer rsETH shares than expected if the oracle rate advances before inclusion. Because rsETH is yield-bearing, receiving fewer shares is a direct reduction in the user's future yield entitlement. The contract does not revert; it silently delivers a worse-than-expected output. The user's ETH is not stolen, but the promised return (the previewed rsETH amount) is not delivered.

## Likelihood Explanation
No attacker action is required. The `InterimRSETHOracle.setRate()` is called by the protocol's `MANAGER_ROLE` as a routine operation to reflect rsETH yield accrual. On L2 networks with variable block times or during periods of congestion, a pending deposit transaction can sit in the mempool long enough for a rate update to be included first. Every depositor on every affected L2 pool contract is exposed on every deposit. The condition is repeatable and requires no special privileges or coordination by any external party.

## Recommendation
Add a `minRsETHAmountExpected` parameter to every `deposit()` overload in `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV2ExternalBridge`, and `RSETHPoolV3ExternalBridge`, mirroring the existing guard in `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply the same pattern to all token `deposit(address,uint256,string)` overloads.

## Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPool` off-chain and observes `rsETHAmount = X`.
2. User submits `deposit{value: 1 ether}("ref")` to `RSETHPool`.
3. Before the transaction is included, the protocol's `MANAGER_ROLE` calls `InterimRSETHOracle.setRate(newRate)` where `newRate > oldRate`.
4. `viewSwapRsETHAmountAndFee` is re-evaluated at execution time with the new rate: `rsETHAmount = (1 ether - fee) * 1e18 / newRate < X`.
5. `IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount)` transfers the reduced amount with no revert, no warning, and no recourse.

**Foundry fork test plan:**
```solidity
function testDepositSlippage() public {
    uint256 preview = pool.viewSwapRsETHAmountAndFee(1 ether);
    // Simulate oracle rate increase
    vm.prank(manager);
    oracle.setRate(oracle.getRate() * 101 / 100); // +1%
    uint256 balBefore = wrsETH.balanceOf(user);
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref");
    uint256 received = wrsETH.balanceOf(user) - balBefore;
    assertLt(received, preview); // user received less than previewed, no revert
}
```