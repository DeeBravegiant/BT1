### Title
Router's Leftover Native ETH Can Be Stolen via Zero-`msg.value` WETH-Input Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the router's **entire** native ETH balance when settling a WETH-input swap, without verifying that the ETH originated from the current transaction's `msg.value`. Any ETH left in the router from a prior user who forgot to call `refundETH` can be silently consumed to pay for a subsequent attacker's swap, giving the attacker a free trade at the prior user's expense.

---

### Finding Description

`pay()` handles WETH-input swaps with the following logic: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

The branch at line 75 (`nativeBalance >= value`) wraps and forwards the router's own ETH without any check that this ETH was deposited by the **current** caller. The router accumulates ETH only from `msg.value` in its `payable` entry points (`multicall`, `exactInputSingle`, `exactOutputSingle`, etc.). If a user sends excess ETH and omits the `refundETH` call, that ETH persists in the router across transactions.

The intended multicall pattern is documented in the test suite: [2](#0-1) 

A user who sends `msg.value = 2 ether` but only needs `amountIn = 1_000` must explicitly call `refundETH` to recover the surplus. If they do not, the surplus is exploitable.

The swap entry point that triggers the callback: [3](#0-2) 

The callback that calls `pay()`: [4](#0-3) 

The pool's `IncorrectDelta` guard only verifies that the pool received the correct WETH amount; it does not care whether the WETH was wrapped from the attacker's ETH or from a prior user's stranded ETH: [5](#0-4) 

---

### Impact Explanation

A prior user's stranded ETH is consumed to settle the attacker's swap. The attacker receives real pool output tokens (token0 or token1) without transferring any asset. The prior user permanently loses the ETH they left in the router. Loss magnitude equals `min(router.balance, attacker's amountIn)` and is bounded only by how much ETH the victim forgot to reclaim.

---

### Likelihood Explanation

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) prevents arbitrary ETH deposits, so the router only accumulates ETH from `msg.value` in payable calls. Users routinely over-send ETH to guarantee a swap succeeds and rely on `refundETH` to recover the surplus. Omitting `refundETH` is a realistic user error, especially for users calling `exactInputSingle` directly (not via multicall). An attacker can monitor the router's ETH balance on-chain and exploit it atomically. [6](#0-5) 

---

### Recommendation

Track the ETH that belongs to the current call context. One approach: inside `pay()`, when `token == WETH`, only consume native ETH up to `msg.value` of the originating call (passed as a parameter or stored in transient storage at entry). Any shortfall must be pulled from the payer via `safeTransferFrom`. Alternatively, enforce that `address(this).balance == 0` at the start of every non-multicall entry point, or require callers to always pair WETH-input swaps with `refundETH` in a multicall and document this as a hard invariant enforced by the router.

---

### Proof of Concept

```
1. Victim calls router.exactInputSingle{value: 10_000}(tokenIn=WETH, amountIn=1_000, ...)
   → swap uses 1_000 ETH worth of WETH; 9_000 ETH remains in router.
   → Victim does NOT call refundETH.

2. Attacker calls router.exactInputSingle{value: 0}(tokenIn=WETH, amountIn=9_000, ...)
   → Pool executes swap, calls metricOmmSwapCallback on router.
   → Router calls pay(WETH, attacker, pool, 9_000).
   → nativeBalance (9_000) >= value (9_000) → branch at line 75 taken.
   → Router wraps its own 9_000 ETH and transfers WETH to pool.
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L257-263)
```text
      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```
