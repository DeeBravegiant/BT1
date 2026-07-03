Audit Report

## Title
Zero rsETH Output on Sub-Threshold Deposits Silently Retains User Funds - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

## Summary

The `deposit()` functions in all three L2 pool contracts compute rsETH output via integer division that can truncate to zero for small-but-nonzero input amounts. No guard checks that `rsETHAmount > 0` before the contract accepts the user's ETH or tokens and mints/transfers zero wrsETH/rsETH. A depositor who sends an amount below the truncation threshold loses their deposited value with no revert and no on-chain warning.

## Finding Description

In `RSETHPoolV3.sol`, `RSETHPoolNoWrapper.sol`, and `RSETHPoolV3ExternalBridge.sol`, `viewSwapRsETHAmountAndFee` computes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // ETH path
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;  // token path
```

When `amountAfterFee * 1e18 < rsETHToETHrate` (currently ≈ 1.05 × 10¹⁸), Solidity integer division truncates `rsETHAmount` to zero. The deposit functions then proceed unconditionally:

- `RSETHPoolV3.sol` line 262: `wrsETH.mint(msg.sender, rsETHAmount)` — mints 0, no revert
- `RSETHPoolNoWrapper.sol` line 241: `rsETH.safeTransfer(msg.sender, rsETHAmount)` — transfers 0, no revert
- `RSETHPoolV3ExternalBridge.sol` line 381: `wrsETH.mint(msg.sender, rsETHAmount)` — mints 0, no revert

The only input guard is `if (amount == 0) revert InvalidAmount()`, which does not protect against a nonzero `amount` that produces zero output. The `limitDailyMint` modifier also does not block this: when `rsETHAmount = 0`, the check `dailyMintAmount + 0 > dailyMintLimit` passes unless the limit is already exhausted, so execution continues. OpenZeppelin's `_mint(account, 0)` and `safeTransfer(addr, 0)` both succeed silently, emitting a `SwapOccurred` event with `rsETHAmount = 0`. The deposited ETH is retained by the pool and credited to no one; for the token path, the ERC20 tokens are transferred into the pool with no corresponding output issued.

By contrast, `LRTDepositPool._beforeDeposit` enforces both a `minAmountToDeposit` floor and a `minRSETHAmountExpected` slippage guard; none of the L2 pool contracts have either.

## Impact Explanation

**Low — Contract fails to deliver promised returns.**

A depositor who sends any ETH amount below `rsETHToETHrate / 1e18` wei (currently ≈ 1 wei, growing as the exchange rate appreciates) receives zero wrsETH/rsETH while their ETH is permanently retained by the pool. The deposited value is not returned and not credited. Because the transaction succeeds and emits a `SwapOccurred` event with `rsETHAmount = 0`, there is no on-chain indication of failure. The per-transaction loss is at most a few wei today, but the truncation threshold grows monotonically with the rsETH exchange rate, and integrators or smart-contract callers that do not inspect the emitted event will silently lose funds.

## Likelihood Explanation

Any unprivileged depositor can trigger this by calling `deposit{value: 1}("")` on any of the three pool contracts. No front-running, oracle manipulation, admin action, or special role is required. The condition is deterministic and reproducible at any time the contracts are unpaused. The affected entry points are public and payable.

## Recommendation

Add a zero-output guard immediately after computing `rsETHAmount` in each `deposit()` overload across all three contracts:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert ZeroRsETHMinted();
```

Apply this to both the ETH and token deposit overloads in `RSETHPoolV3`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge`. Optionally, add a `minRSETHAmountExpected` parameter (as `LRTDepositPool._beforeDeposit` does) to give callers explicit slippage control.

## Proof of Concept

Assume `rsETHToETHrate = 1.05e18` (realistic current value):

1. Alice calls `RSETHPoolV3.deposit{value: 1}("")`.
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * feeBps / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation)
3. `limitDailyMint` modifier: `dailyMintAmount + 0 > dailyMintLimit` → false, execution continues.
4. `feeEarnedInETH += 0`
5. `wrsETH.mint(Alice, 0)` — OZ `_mint` with amount 0 succeeds, Alice receives 0 wrsETH.
6. Alice's 1 wei ETH is now held by the pool with no wrsETH issued.
7. `SwapOccurred(Alice, 0, 0, "")` is emitted — no revert, no indication of failure.

Foundry fuzz test sketch:
```solidity
function testFuzz_zeroOutputDeposit(uint256 amount) public {
    uint256 rate = pool.getRate(); // e.g. 1.05e18
    vm.assume(amount > 0 && amount * 1e18 < rate); // sub-threshold
    vm.deal(alice, amount);
    vm.prank(alice);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(alice), 0); // Alice got nothing
    assertEq(address(pool).balance, amount); // Pool kept ETH
}
```