Audit Report

## Title
Zero wrsETH/rsETH Minted for Non-Zero Deposit Due to Integer Division Truncation - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `viewSwapRsETHAmountAndFee` function in all L2 pool contracts computes output amounts using integer division without a zero-output guard. When a depositor sends a sufficiently small ETH or token amount such that `amountAfterFee * 1e18 < rsETHToETHrate`, the computed `rsETHAmount` truncates to zero. The deposit functions only guard against `amount == 0`, not `rsETHAmount == 0`, so the transaction proceeds: the user's ETH is absorbed into the pool and they receive zero `wrsETH` or `rsETH` in return.

## Finding Description
In `RSETHPoolV3.sol`, the ETH deposit path at lines 246–265 checks `if (amount == 0) revert InvalidAmount()` but does not check the output of `viewSwapRsETHAmountAndFee`. The rate computation at lines 299–308 performs:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

With `rsETHToETHrate ≈ 1.1e18` and `amountAfterFee = 1`, this yields `1 * 1e18 / 1.1e18 = 0`. Control then reaches `wrsETH.mint(msg.sender, 0)` at line 262, which succeeds silently. The user's ETH remains in the pool's balance. The identical pattern is confirmed in:

- `RSETHPoolNoWrapper.sol` lines 231–244, 277–286 (`rsETH.safeTransfer(msg.sender, 0)` at line 241)
- `RSETHPoolV3ExternalBridge.sol` lines 366–384, 418–427
- Token deposit overloads in all variants (lines 271–293 in `RSETHPoolV3.sol`, lines 250–271 in `RSETHPoolNoWrapper.sol`, lines 390–412 in `RSETHPoolV3ExternalBridge.sol`)

The token path (`amountAfterFee * tokenToETHRate / rsETHToETHrate`) is equally susceptible when `tokenToETHRate` is small relative to `rsETHToETHrate`.

## Impact Explanation
**Low — Contract fails to deliver promised returns.** A depositor sending a non-zero amount receives zero `wrsETH`/`rsETH`. Their deposited ETH (or tokens) is credited to the pool's bridgeable balance with no corresponding user claim. The per-transaction loss is bounded by `rsETHToETHrate / 1e18` wei (≈1–2 wei at current rates), so individual losses are negligible. However, the invariant that every non-zero deposit yields a non-zero output is broken.

## Likelihood Explanation
Any unprivileged depositor can trigger this by calling `deposit{value: 1}("")` with no special setup, front-running, or coordination. The condition `amountAfterFee * 1e18 < rsETHToETHrate` is met organically for any deposit of 1 wei when `feeBps = 0`. As `rsETHToETHrate` grows over time (rsETH accrues value), the threshold rises slightly, marginally increasing the range of inputs that trigger truncation.

## Recommendation
Add a zero-output guard in each `deposit` function immediately after computing `rsETHAmount`:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

Apply the same guard to the token deposit overload and to all pool contract variants (`RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`).

## Proof of Concept
Using `RSETHPoolV3.sol` with `rsETHToETHrate = 1.1e18` and `feeBps = 0`:

1. Alice calls `deposit{value: 1}("")`.
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * 0 / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.1e18 = 0` (Solidity integer truncation)
3. `if (amount == 0)` check passes (amount is 1).
4. `feeEarnedInETH += 0`.
5. `wrsETH.mint(msg.sender, 0)` — succeeds, mints nothing.
6. Alice's 1 wei is in the pool; she holds no `wrsETH`.

Foundry fuzz test sketch:
```solidity
function testFuzz_zeroMintOnSmallDeposit(uint256 amount) public {
    vm.assume(amount > 0 && amount * 1e18 < pool.getRate());
    vm.deal(alice, amount);
    vm.prank(alice);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(alice), 0);
    assertEq(address(pool).balance, amount);
}
```