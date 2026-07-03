Audit Report

## Title
Zero rsETH/wrsETH Minted on Dust Token Deposits Due to Integer Division Truncation - (File: contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

## Summary
All five L2 pool contracts compute the output rsETH/wrsETH amount via a single integer division `amountAfterFee * tokenToETHRate / rsETHToETHrate` that truncates to zero when the deposit amount is below a token-specific dust threshold. The deposited tokens are already transferred to the pool before the output is computed, and the only input guard (`if (amount == 0) revert InvalidAmount()`) does not catch the case where the *output* truncates to zero. The user's deposited tokens remain permanently in the pool with no recourse.

## Finding Description
The vulnerable pattern is identical across all five contracts. In `RSETHPoolNoWrapper.sol`:

```solidity
// L260 — only guards against zero input, not zero output
if (amount == 0) revert InvalidAmount();

// L262 — tokens leave the user before output is computed
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

// L264 — output computed after transfer
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

// L268 — silently transfers 0 if truncated (OZ ERC-20 allows zero-amount transfers)
rsETH.safeTransfer(msg.sender, rsETHAmount);
```

The truncation occurs in `viewSwapRsETHAmountAndFee` (L311):
```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

When `amountAfterFee * tokenToETHRate < rsETHToETHrate` (≈1.05e18), Solidity integer division yields `rsETHAmount = 0`. The same pattern is confirmed at:
- `RSETHPoolV3.sol` L334
- `RSETHPool.sol` L346
- `RSETHPoolV3ExternalBridge.sol` L452
- `RSETHPoolV3WithNativeChainBridge.sol` L370

For `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`, the call is `wrsETH.mint(msg.sender, 0)` — minting nothing. For `RSETHPool`, it is `IERC20(address(wrsETH)).safeTransfer(msg.sender, 0)` — also a no-op. In all paths the deposited tokens remain in the pool and are swept into the bridging balance, irrecoverably lost to the user.

No minimum deposit amount guard exists in any of these contracts. The `limitDailyMint` modifier present on some variants checks the input `amount`, not the computed output, and does not prevent this.

## Impact Explanation
**Severity: Low — Contract fails to deliver promised returns.**

The user deposits tokens and receives zero rsETH/wrsETH in return. The deposited tokens are permanently retained by the pool. The monetary loss is bounded by the truncation threshold:
- 18-decimal tokens (stETH, wstETH): < 2 wei
- 6-decimal tokens (USDC-like): < ~3 000 wei (≈ 0.003 USDC)

These are dust-level amounts. The contract silently accepts the deposit and emits a `SwapOccurred` event with `rsETHAmount = 0`, which is misleading but not catastrophic. This maps to the allowed impact: **Low — Contract fails to deliver promised returns**.

## Likelihood Explanation
Any unprivileged depositor can trigger this by calling `deposit(token, dustAmount, referralId)` with an amount below the truncation threshold for any supported token. No special role, precondition, or privileged access is required. Accidental triggering is unlikely (users rarely send dust), but deliberate triggering is trivially easy and repeatable.

## Recommendation
Add a post-computation output guard in every pool's token-deposit function immediately after `viewSwapRsETHAmountAndFee` is called:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
if (rsETHAmount == 0) revert InvalidAmount();
```

This should be applied to all five contracts at the identified deposit functions. Alternatively, enforce a per-token minimum deposit amount analogous to `minAmountToDeposit` in `LRTDepositPool`.

## Proof of Concept
Using `RSETHPoolNoWrapper` with a 6-decimal token (e.g., USDC), `feeBps = 0`, `rsETHToETHrate = 1.05e18`, `tokenToETHRate = 3.33e14` (1 USDC ≈ 1/3000 ETH):

1. User calls `deposit(USDC, 3000, "")` — depositing 3000 wei (0.003 USDC).
2. `amount == 0` check passes (3000 ≠ 0).
3. `IERC20(USDC).safeTransferFrom(user, pool, 3000)` — succeeds; 3000 wei USDC leaves user.
4. `fee = 3000 * 0 / 10_000 = 0`; `amountAfterFee = 3000`.
5. `rsETHAmount = 3000 * 3.33e14 / 1.05e18 = 999_000_000_000_000_000 / 1_050_000_000_000_000_000 = 0` (truncated).
6. `rsETH.safeTransfer(user, 0)` — succeeds silently; user receives nothing.
7. `SwapOccurred(user, 0, 0, "")` emitted — user has permanently lost 3000 wei USDC.

**Foundry fuzz test plan:**
```solidity
function testFuzz_dustDepositTruncatesToZero(uint256 amount) public {
    vm.assume(amount > 0 && amount < threshold); // threshold = rsETHToETHrate / tokenToETHRate
    uint256 balanceBefore = rsETH.balanceOf(user);
    vm.prank(user);
    pool.deposit(address(usdc), amount, "");
    assertEq(rsETH.balanceOf(user), balanceBefore); // user received nothing
    assertGt(usdc.balanceOf(address(pool)), 0);     // pool holds user's tokens
}
```