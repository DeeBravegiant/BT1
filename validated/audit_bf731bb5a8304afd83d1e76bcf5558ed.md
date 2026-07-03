Audit Report

## Title
Zero wrsETH/rsETH Minted on Dust Deposits Due to Integer Division Truncation - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

## Summary
Every L2 deposit pool computes the output wrsETH/rsETH amount via integer division (`amountAfterFee * 1e18 / rsETHToETHrate`). When the deposited amount is smaller than `rsETHToETHrate / 1e18`, the division truncates to zero. The deposit functions guard only against a zero input amount, not a zero output amount, so the transaction succeeds while the user receives zero wrsETH/rsETH and permanently loses their deposited ETH to the pool.

## Finding Description
In all four pool variants, `viewSwapRsETHAmountAndFee` computes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

`rsETHToETHrate` is always ≥ 1e18 (rsETH appreciates monotonically). For any `amountAfterFee` where `amountAfterFee * 1e18 < rsETHToETHrate` (e.g., `amountAfterFee = 1` when rate = 1.05e18), integer division yields `rsETHAmount = 0`.

The deposit functions in all four contracts check only:

```solidity
if (amount == 0) revert InvalidAmount();
```

There is no subsequent check on `rsETHAmount`. Execution proceeds to either:
- `wrsETH.mint(msg.sender, 0)` — `RsETHTokenWrapper.mint` calls `_mint` with no zero-amount guard, succeeds silently.
- `rsETH.safeTransfer(msg.sender, 0)` — ERC-20 transfer of 0 is valid, succeeds silently.

The user's ETH (or token) is accepted by the contract, credited to the pool's balance, and never recoverable by the user. The same truncation pattern is confirmed in `RSETHPoolV3.sol` (L307), `RSETHPoolNoWrapper.sol` (L285), `RSETHPoolV3ExternalBridge.sol` (L426), and `RSETHPoolV3WithNativeChainBridge.sol` (L343).

## Impact Explanation
**Low. Contract fails to deliver promised returns, but doesn't lose value.**

A depositor sending a dust amount of ETH receives zero wrsETH/rsETH. Their ETH is permanently retained by the pool (pool balance increases, user balance is unchanged). No revert occurs, giving the user no on-chain signal that the deposit was economically worthless. The protocol does not lose value; the user suffers a direct loss equal to the deposited dust amount.

## Likelihood Explanation
Low. The affected amounts are sub-wei to a few wei of ETH under normal conditions (rate slightly above 1e18). Accidental triggering by normal users is unlikely. However, the path is fully permissionless, requires no special privileges, and is always reachable after protocol launch since `rsETHToETHrate` is always ≥ 1e18. A malicious actor could repeatedly trigger this to accumulate unattributed dust ETH in the pool.

## Recommendation
Add a zero-output guard immediately after computing `rsETHAmount` in every `deposit` function and in `viewSwapRsETHAmountAndFee` across all four pool contracts:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

This ensures the transaction reverts whenever the deposited amount is too small to receive at least one unit of wrsETH/rsETH.

## Proof of Concept
Assume `rsETHToETHrate = 1.05e18` (5% appreciation) and `feeBps = 0`.

1. User calls `RSETHPoolV3.deposit{value: 1}("")` (1 wei ETH).
2. `viewSwapRsETHAmountAndFee(1)`:
   - `fee = 1 * 0 / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation)
3. `feeEarnedInETH += 0` (no change).
4. `wrsETH.mint(msg.sender, 0)` — succeeds, user receives 0 wrsETH.
5. `emit SwapOccurred(msg.sender, 0, 0, referralId)` — event emitted with zero amounts.
6. User has lost 1 wei of ETH; pool balance increased by 1 wei; no revert.

**Foundry fuzz test plan:**
```solidity
function testFuzz_dustDepositYearsZeroMint(uint256 amount) public {
    amount = bound(amount, 1, rsETHToETHrate / 1e18); // dust range
    uint256 balBefore = wrsETH.balanceOf(user);
    vm.prank(user);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(user), balBefore); // user received nothing
    assertGt(address(pool).balance, 0);           // pool kept the ETH
}
```