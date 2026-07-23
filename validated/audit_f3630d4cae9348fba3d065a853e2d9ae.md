The code is confirmed. Let me verify the exact execution path for `exactOutputSingle` to ensure the residual ETH accounting is correct.

Audit Report

## Title
Residual ETH in Router Silently Subsidizes Subsequent WETH Swaps, Stealing Prior User's ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` reads `address(this).balance` with no per-transaction accounting when settling WETH swap inputs. ETH left in the router from a prior user's payable call (e.g., excess ETH sent to `exactOutputSingle` without a subsequent `refundETH`) is silently consumed to partially fund a later user's WETH payment, causing the prior user to lose their stranded ETH with no revert, no event, and no recovery path.

## Finding Description
The `pay` function's WETH branch at `PeripheryPayments.sol` lines 74–84 reads `nativeBalance = address(this).balance` — the total contract ETH — and uses it unconditionally when `nativeBalance > 0`. All swap entry points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`) are `payable`, so users routinely send excess ETH for exact-output swaps expecting to reclaim it via `refundETH()`. Any ETH not reclaimed in the same multicall persists across transaction boundaries.

The transient storage context (`TransientCallbackPool`) tracks only the callback pool, payer address, token, and mode — it does not record how much ETH the current transaction contributed, and there is no cap on native ETH consumption per call. The `receive()` guard at lines 32–34 blocks direct ETH pushes from non-WETH addresses but does not prevent accumulation via payable entry points.

Exploit flow:
1. User A calls `exactOutputSingle{value: 1000}` where the pool's actual input is 600. `_justPayCallback` → `pay(WETH, userA, pool, 600)`: `nativeBalance=1000 >= 600`, wraps 600 ETH, sends to pool. 400 ETH remains in router.
2. User B calls `exactInputSingle` with `tokenIn=WETH`, `amountIn=800`, sending no ETH. `_justPayCallback` → `pay(WETH, userB, pool, 800)`: `nativeBalance=400 > 0`, wraps 400 ETH, sends to pool, then pulls only 400 WETH from User B via `safeTransferFrom`. Pool receives 800 WETH; User B pays 400 WETH instead of 800; User A's 400 ETH is gone.

## Impact Explanation
Direct loss of user principal. User A's stranded ETH is transferred to the pool as WETH on behalf of User B with no event, no revert, and no recovery path. Per-user settlement conservation is broken: User B's net WETH debit is `value - residual` instead of `value`. Pool solvency is unaffected (it receives the correct `value`), but the router incorrectly attributes ETH belonging to User A toward User B's obligation.

## Likelihood Explanation
Moderate. The exact-output payable pattern (`exactOutputSingle{value: X}` where X > actual input) is the standard native-ETH usage pattern documented in the test suite (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth` explicitly shows excess ETH stranded without `refundETH`). Users who omit `refundETH()` — by mistake, via a frontend that doesn't batch it, or due to a failed multicall that reverts after the swap — leave ETH stranded. Any subsequent WETH swap by any address drains it. An attacker can monitor the router's ETH balance on-chain and immediately submit a WETH swap to capture the subsidy.

## Recommendation
Track the ETH contributed by the current transaction in transient storage at each payable entry point (storing `msg.value`), and cap native ETH consumption in `pay` to that per-call budget rather than `address(this).balance`. Alternatively, pass the caller-contributed ETH amount explicitly through the call stack so `pay` can distinguish owned ETH from residual ETH.

## Proof of Concept
```solidity
// 1. User A: exact-output WETH swap, sends excess ETH, omits refundETH
router.exactOutputSingle{value: 1000}(ExactOutputSingleParams({
    tokenIn: WETH, amountOut: someOutput, amountInMaximum: 1000, ...
}));
// actual input consumed = 600; router.balance = 400 (User A's ETH, stranded)

// 2. User B: exact-input WETH swap, sends no ETH
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 800, ...
}));
// pay(WETH, userB, pool, 800) called in callback
// nativeBalance = 400 → wraps 400 ETH → safeTransfer(pool, 400)
// safeTransferFrom(userB, pool, 400) ← userB pays only 400, not 800
// pool receives 800 WETH ✓, userB saves 400 WETH, userA loses 400 ETH
assert(weth.balanceOf(userB_before) - weth.balanceOf(userB_after) == 400); // not 800
assert(address(router).balance == 0); // User A's ETH is gone
```