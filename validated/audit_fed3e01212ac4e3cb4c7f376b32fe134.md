### Title
Leftover `msg.value` ETH on `MetricOmmSimpleRouter` / `MetricOmmPoolLiquidityAdder` Can Be Consumed by Any Subsequent WETH-Input Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` (the contract's entire native ETH balance) to subsidise WETH-input payments. Because the payable entry-points (`exactInputSingle`, `exactOutputSingle`, `multicall`, `addLiquidityExactShares`, etc.) can receive more ETH than is actually consumed in a single call, any unrefunded ETH left on the contract from a prior user's transaction is silently spent on behalf of the next caller who specifies WETH as the input token.

---

### Finding Description

`PeripheryPayments.pay()` contains the following branch for WETH payments:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← ALL ETH on the contract
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
``` [1](#0-0) 

The `receive()` guard correctly blocks plain ETH transfers from non-WETH senders:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

However, `receive()` only applies to plain ETH transfers. ETH sent via `msg.value` through any `payable` function (e.g. `exactInputSingle`, `multicall`, `addLiquidityExactShares`) bypasses this guard entirely and lands on the contract. If the user sends more ETH than the swap consumes and does **not** call `refundETH()` in the same multicall, the surplus ETH persists on the contract across transaction boundaries.

The next caller who invokes any WETH-input swap with `msg.value = 0` will have their payment fully or partially covered by the prior user's stranded ETH, because `pay()` reads `address(this).balance` — the aggregate balance — rather than only the ETH that arrived in the current call.

The `_justPayCallback` path (used by `exactInputSingle` / `exactInput`) and the `_exactOutputIterateCallback` path (used by `exactOutputSingle` / `exactOutput`) both reach `pay()` with the original `msg.sender` as `payer`: [3](#0-2) [4](#0-3) 

The same `pay()` function is shared by `MetricOmmPoolLiquidityAdder`: [5](#0-4) 

---

### Impact Explanation

A user who sends excess ETH (e.g. `exactInputSingle{value: 2 ether}(amountIn: 1 ether, tokenIn: WETH)`) without a `refundETH()` call in the same multicall leaves 1 ETH stranded on the router. Any subsequent caller can call `exactInputSingle{value: 0}(amountIn: 1 ether, tokenIn: WETH)` and have their entire swap input paid from the stranded ETH. The original depositor permanently loses their ETH; the attacker receives the swap output for free. This is a direct loss of user principal with no protocol-level recovery path.

Additionally, `refundETH()` sends `address(this).balance` to `msg.sender` with no access control, so a racing attacker can also drain stranded ETH directly: [6](#0-5) 

---

### Likelihood Explanation

The pattern of sending ETH via `msg.value` to cover a WETH-input swap is explicitly supported and tested by the protocol (see `test_multicall_ethInput_exactInputSingle_refundsUnusedEth`). The refund step is optional and must be included manually in a multicall. Any user who calls a payable swap function directly (not via multicall) or whose multicall omits `refundETH()` will leave ETH on the contract. MEV bots monitoring the mempool can detect such transactions and immediately follow up with a zero-cost WETH swap or a direct `refundETH()` call. [7](#0-6) 

---

### Recommendation

Track the ETH that belongs to the current call using transient storage. At the entry of each payable function, record `msg.value` in a transient slot. In `pay()`, read only that recorded amount rather than `address(this).balance`:

```solidity
} else if (token == WETH) {
    uint256 callEth = _getMsgValueForThisCall(); // transient-stored msg.value
    if (callEth >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
        _decreaseMsgValue(value);
    } else if (callEth > 0) {
        IWETH9(WETH).deposit{value: callEth}();
        IERC20(WETH).safeTransfer(recipient, callEth);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - callEth);
        _decreaseMsgValue(callEth);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

This ensures that only ETH sent in the current transaction can subsidise the current caller's payment, eliminating cross-user ETH leakage.

---

### Proof of Concept

```
1. Alice calls exactInputSingle{value: 2 ether}(amountIn: 1 ether, tokenIn: WETH, ...)
   → pay() sees nativeBalance = 2 ether >= 1 ether
   → wraps 1 ether, sends WETH to pool
   → 1 ether remains on the router (Alice forgot refundETH)

2. Bob calls exactInputSingle{value: 0}(amountIn: 1 ether, tokenIn: WETH, ...)
   → pay() sees nativeBalance = 1 ether >= 1 ether
   → wraps Alice's 1 ether, sends WETH to pool on Bob's behalf
   → Bob receives swap output; Alice's 1 ether is gone

Net result: Alice loses 1 ETH; Bob receives a free swap.
``` [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L207-213)
```text
    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
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
