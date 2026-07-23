Audit Report

## Title
Router's Stranded Native ETH Consumed for Attacker's WETH-Input Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` settles WETH-input swaps by wrapping the router's entire `address(this).balance` without verifying the ETH belongs to the current caller's `msg.value`. ETH left in the router by a prior user who omitted `refundETH` persists across transactions and can be silently consumed to fund a subsequent attacker's swap, giving the attacker real pool output tokens at zero cost.

## Finding Description

In `pay()` at [1](#0-0) , when `token == WETH` and `address(this).balance >= value`, the router wraps its own ETH and transfers WETH to the pool with no check that the ETH originated from the current call's `msg.value`. The payer address stored in transient storage is only used in the `safeTransferFrom` fallback branches (lines 81, 83); the first branch bypasses it entirely.

The entry point `exactInputSingle` is `payable` and stores `msg.sender` as the payer via `_setNextCallbackContext` [2](#0-1) , but never enforces that `msg.value` covers `amountIn`. The callback `_justPayCallback` calls `pay()` with the attacker as `payer` [3](#0-2) , but `pay()` never reaches `safeTransferFrom(payer, ...)` when `nativeBalance >= value`.

The `receive()` guard [4](#0-3)  prevents arbitrary direct ETH deposits but does not prevent ETH from accumulating via `msg.value` in payable entry points. A victim who sends excess ETH to `exactInputSingle` and omits `refundETH` leaves that ETH stranded in the router indefinitely. The `refundETH` function [5](#0-4)  is entirely optional and caller-initiated.

The pool's `IncorrectDelta` guard only verifies the pool received the correct token amount; it does not verify the source of the WETH used to settle the swap.

## Impact Explanation

A prior user's stranded ETH is consumed to settle the attacker's swap. The attacker receives real pool output tokens without transferring any asset. The victim permanently loses the ETH they left in the router. Loss magnitude equals `min(router.balance, attacker's amountIn)` and is bounded only by how much ETH the victim forgot to reclaim. This is a direct loss of user principal with no recovery path, meeting Critical/High severity thresholds.

## Likelihood Explanation

The attack requires only that a prior user sent excess ETH and omitted `refundETH` — a realistic user error, especially for direct (non-multicall) `exactInputSingle` calls. The router's ETH balance is publicly readable on-chain, so an attacker can monitor it and exploit it atomically in the next block. No privileged access or special setup is required; any unprivileged caller can trigger this.

## Recommendation

Track the ETH attributable to the current call context. Inside `pay()`, when `token == WETH`, only consume native ETH up to the `msg.value` of the originating call (passed as a parameter or stored in transient storage at entry). Any shortfall must be pulled from the payer via `safeTransferFrom`. Alternatively, enforce `address(this).balance == 0` at the start of every non-multicall payable entry point, or require callers to always pair WETH-input swaps with `refundETH` in a multicall and enforce this as a hard invariant in the router.

## Proof of Concept

```
1. Victim calls router.exactInputSingle{value: 10_000}(tokenIn=WETH, amountIn=1_000, ...)
   → swap uses 1_000 ETH worth of WETH; 9_000 ETH remains in router.
   → Victim does NOT call refundETH.

2. Attacker calls router.exactInputSingle{value: 0}(tokenIn=WETH, amountIn=9_000, ...)
   → Pool executes swap, calls metricOmmSwapCallback on router.
   → Router calls pay(WETH, attacker, pool, 9_000).
   → nativeBalance (9_000) >= value (9_000) → branch at line 75 taken.
   → Router wraps its own 9_000 ETH and transfers WETH to pool.
   → safeTransferFrom(attacker, ...) is never called.
   → Pool's IncorrectDelta check passes (pool received correct WETH).
   → Attacker receives pool output tokens.

Result: Attacker paid 0 ETH; victim lost 9_000 ETH.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
