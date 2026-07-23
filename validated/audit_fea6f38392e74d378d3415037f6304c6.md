### Title
Router `PeripheryPayments.pay()` consumes unattributed native ETH balance, enabling theft of stranded ETH from prior users — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` without any per-user attribution. When `token == WETH` and the router holds native ETH from a prior user's `msg.value` overpayment, a subsequent caller can have their entire WETH swap settled using that stranded ETH — paying nothing themselves — while the prior user permanently loses their funds.

---

### Finding Description

The `pay()` function in `PeripheryPayments.sol` handles WETH payments by first checking the router's native ETH balance:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // no pull from payer
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // partial pull
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the router wraps and forwards the native ETH to the pool **without calling `safeTransferFrom` on the payer at all**. The payer identity stored in transient storage is ignored for the actual token pull. The router has no mechanism to track which user's `msg.value` contributed to the current native balance.

All public swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable`, so users routinely send ETH with `msg.value` to pay for WETH-input swaps. The intended pattern is to include a `refundETH()` call in the same `multicall` to recover any excess. If a user omits `refundETH`, the excess ETH is stranded in the router and is immediately claimable by any subsequent WETH-input swap caller. [2](#0-1) [3](#0-2) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks direct ETH pushes; it does not prevent ETH from accumulating via `msg.value` on payable functions. [4](#0-3) 

---

### Impact Explanation

A victim who sends `msg.value = X + Y` with `amountIn = X` (WETH) strands `Y` ETH in the router. An attacker who calls `exactInputSingle{value: 0}` with `tokenIn = WETH` and `amountIn ≤ Y` receives the full swap output without paying any ETH or WETH. The pool's `IncorrectDelta` check passes because the pool receives the correct WETH amount (wrapped from the victim's stranded ETH). The victim's `Y` ETH is permanently lost.

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all are `payable` and all route through the same `pay()` function in their callbacks. [5](#0-4) [6](#0-5) 

This is a direct loss of user principal above Sherlock thresholds whenever a user overpays `msg.value` without a `refundETH` step.

---

### Likelihood Explanation

The documented usage pattern (confirmed by the test suite) is `multicall{value: amountIn}(exactInputSingle(...))` with an optional `refundETH` as a second call. Users who send a round-number ETH value slightly above the exact swap cost, or who construct the multicall without a `refundETH` step, will strand ETH. An attacker can monitor the mempool or the router's ETH balance and front-run or follow up with a zero-cost WETH swap. No special privileges, approvals, or setup are required. [7](#0-6) 

---

### Recommendation

**Short term:** In `pay()`, cap the native ETH consumed to the amount the current `msg.sender` contributed in this transaction. Store the `msg.value` of the outermost payable call in a transient slot at entry (e.g., in `multicall` or each payable swap function) and deduct from it as ETH is used. Only use native ETH up to that tracked budget; pull the remainder from the payer via `safeTransferFrom`.

**Long term:** Automatically refund any unused `msg.value` at the end of each top-level payable call (i.e., at the end of `multicall` or each individual swap function), eliminating the possibility of stranded ETH between transactions.

---

### Proof of Concept

```
1. Victim calls:
   router.multicall{value: 2 ETH}([
     exactInputSingle(tokenIn=WETH, amountIn=1 ETH, ...)
     // no refundETH call
   ])
   → pay() wraps 1 ETH, sends to pool. 1 ETH stranded in router.

2. Attacker calls (in a later tx):
   router.exactInputSingle{value: 0}(
     tokenIn=WETH, amountIn=1 ETH, recipient=attacker, ...
   )
   → pay() sees address(this).balance = 1 ETH >= 1 ETH
   → wraps victim's 1 ETH, safeTransfers WETH to pool
   → no safeTransferFrom on attacker (branch never reached)
   → pool's IncorrectDelta check passes (pool received correct WETH)
   → attacker receives swap output for free

Net: victim loses 1 ETH; attacker receives token1 output worth ~1 ETH at oracle price.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-87)
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
