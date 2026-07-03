Audit Report

## Title
Zero wrsETH Minted for Non-Zero Deposit Due to Integer Division Rounding - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `viewSwapRsETHAmountAndFee` function in all pool contracts computes output amounts using plain integer division without a zero-output guard. A depositor sending a sufficiently small but non-zero ETH or token amount will have their assets transferred to the pool while receiving zero `wrsETH`/`rsETH` in return. The deposit functions only guard against `amount == 0`, not against `rsETHAmount == 0`.

## Finding Description
In `RSETHPoolV3.sol`, `viewSwapRsETHAmountAndFee` computes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;  // L307
```

When `amountAfterFee * 1e18 < rsETHToETHrate` (e.g., `amountAfterFee = 1` wei and `rsETHToETHrate ≈ 1.1e18`), integer division truncates to zero. The ETH-path `deposit` function at L246–L265 only checks `if (amount == 0) revert InvalidAmount()` at L256, then proceeds to call `wrsETH.mint(msg.sender, rsETHAmount)` at L262 with `rsETHAmount = 0`. The user's ETH is already credited to the pool's balance (via `msg.value`) before the mint, so the user receives nothing. The identical pattern exists in:
- `RSETHPoolV3.sol` token deposit (L271–L293, L315–L335)
- `RSETHPoolNoWrapper.sol` ETH deposit (L231–L244, L277–L286) — calls `rsETH.safeTransfer(msg.sender, 0)`
- `RSETHPoolV3ExternalBridge.sol` ETH deposit (L366–L384, L418–L427)

The existing `amount == 0` guard is insufficient because it only prevents a zero-input deposit, not a zero-output deposit caused by rate-induced truncation.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**
A depositor sending 1 wei of ETH receives 0 `wrsETH`. Their 1 wei remains in the pool's bridgeable ETH balance, unrecoverable by the user. The maximum loss per transaction is bounded by `rsETHToETHrate / 1e18` wei (≈1–2 wei at current rates), making individual losses negligible. However, the invariant that a non-zero deposit always yields a non-zero output is concretely violated.

## Likelihood Explanation
Any unprivileged depositor can trigger this by calling `deposit{value: 1}("")`. No special setup, front-running, or coordination is required. The condition `amountAfterFee * 1e18 < rsETHToETHrate` is met whenever `amountAfterFee < rsETHToETHrate / 1e18`. As `rsETHToETHrate` grows over time (rsETH accrues value), the threshold increases marginally. The trigger is deterministic and repeatable.

## Recommendation
Add a zero-output guard in each `deposit` function immediately after computing `rsETHAmount`:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

Apply this guard to both the ETH and token deposit overloads in all pool contract variants (`RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPool`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`).

## Proof of Concept
Using `RSETHPoolV3.sol` with `rsETHToETHrate = 1.1e18` and `feeBps = 0`:

1. Alice calls `deposit{value: 1}("")`.
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * 0 / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.1e18 = 0` (truncated)
3. `if (amount == 0)` check passes (amount is 1).
4. `wrsETH.mint(msg.sender, 0)` executes — Alice receives 0 `wrsETH`.
5. Alice's 1 wei is permanently in the pool's ETH balance.

**Foundry fuzz test plan:**
```solidity
function testFuzz_zeroMintOnSmallDeposit(uint256 amount) public {
    vm.assume(amount > 0 && amount < rsETHToETHrate / 1e18 + 1);
    uint256 balBefore = wrsETH.balanceOf(alice);
    vm.deal(alice, amount);
    vm.prank(alice);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(alice), balBefore); // receives 0 wrsETH
}
```