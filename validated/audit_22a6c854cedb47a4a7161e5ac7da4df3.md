Audit Report

## Title
Missing Minimum Output Amount (Slippage Protection) in Pool `deposit()` Functions - (File: contracts/pools/RSETHPool.sol, RSETHPoolV2.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV2NBA.sol, RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolNoWrapper.sol)

## Summary
All L2 pool `deposit()` functions compute the rsETH output amount from a live oracle rate at execution time but accept no `minRsETHAmount` parameter, giving callers no on-chain mechanism to reject a transaction whose output has fallen below their acceptable threshold. The L1 `LRTDepositPool` already enforces this protection via `minRSETHAmountExpected`, but none of the L2 pool contracts replicate it. A user who previews the rate off-chain and submits a deposit may receive materially fewer wrsETH/rsETH tokens than expected if the oracle rate moves before the transaction is mined.

## Finding Description
Every L2 pool `deposit()` entry point follows the same pattern: it calls `viewSwapRsETHAmountAndFee`, which reads the live oracle rate via `getRate()` at execution time, and immediately transfers the computed `rsETHAmount` to the caller with no floor check.

Confirmed in `RSETHPool.sol` L265–278:
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```
And `viewSwapRsETHAmountAndFee` at L311–320:
```solidity
uint256 rsETHToETHrate = getRate();   // live oracle read
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
The same pattern is present in `RSETHPoolV3.sol` L246–293, `RSETHPoolNoWrapper.sol` L231–271, and all other listed variants.

By contrast, `LRTDepositPool.depositETH` (L76–93) and `depositAsset` (L99–118) both pass `minRSETHAmountExpected` into `_beforeDeposit`, which reverts at L667–669 if `rsethAmountToMint < minRSETHAmountExpected`. No equivalent guard exists in any L2 pool contract.

The root cause is a missing parameter and a missing revert branch. No privileged access, oracle manipulation, or attacker is required; ordinary staking-reward-driven rate drift between transaction submission and execution is sufficient to trigger the discrepancy.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

When the rsETH/ETH oracle rate increases between a user's transaction submission and its execution, the user receives fewer wrsETH/rsETH tokens than they observed off-chain. Their deposited ETH is fully consumed; the tokens they receive are worth the same total ETH value at the new rate, so no ETH value is destroyed. However, the contract does not deliver the token quantity the user was promised at submission time, and the user has no on-chain recourse to abort the transaction. This matches the allowed Low impact: "Contract fails to deliver promised returns, but doesn't lose value."

## Likelihood Explanation
The rsETH/ETH rate increases continuously as staking rewards accrue and as the underlying LST basket rebalances. On congested L2 networks, transactions can remain pending for multiple blocks. The deposit entry points are fully permissionless — every user who calls `deposit()` is exposed. No attacker action, oracle compromise, or governance capture is required; the condition arises from normal protocol operation.

## Recommendation
Add a `minRsETHAmount` parameter to every public `deposit()` function in all pool contract variants and revert if the computed output falls below it, mirroring the pattern in `LRTDepositPool._beforeDeposit()`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert MinimumAmountToReceiveNotMet();

    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```
Apply the same pattern to the token-deposit overload (`deposit(address,uint256,string)`) in all pool variants.

## Proof of Concept
1. Alice queries `RSETHPoolV3.viewSwapRsETHAmountAndFee(10 ether)` off-chain; `getRate()` returns `1.05e18`, so she expects ≈ 9.52 wrsETH (after fee).
2. Alice submits `RSETHPoolV3.deposit{value: 10 ether}("ref")`.
3. Before her transaction is mined, staking rewards cause the oracle rate to update to `1.10e18`.
4. Her transaction executes: `rsETHAmount = (10e18 - fee) * 1e18 / 1.10e18 ≈ 9.09 wrsETH` — approximately 4.5% fewer tokens than expected.
5. Because no `minRsETHAmount` parameter exists, Alice had no on-chain mechanism to revert the transaction.

**Foundry fork test plan:**
- Fork a network where the pool is deployed.
- Call `deposit{value: 10 ether}` in a test.
- Between submission and execution (using `vm.mockCall` or a state-manipulating cheat), increase the value returned by `getRate()` by ~5%.
- Assert that `rsETHAmount` received is less than the amount computed at the original rate, and that no revert occurred.