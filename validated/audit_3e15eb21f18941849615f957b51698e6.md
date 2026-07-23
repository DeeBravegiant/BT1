### Title
Native ETH Sent with Non-WETH ERC20 Swap Calls Is Silently Stranded on the Router and Stealable by Anyone via `refundETH()` - (File: `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

All four swap entry points in `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are declared `payable`. When a caller sends native ETH alongside a swap whose `tokenIn` is a plain ERC20 (not WETH), the `pay()` helper in `PeripheryPayments` silently ignores the ETH and settles the swap via `safeTransferFrom`. The ETH is left stranded on the router. Because `refundETH()` is a public function that transfers the entire router ETH balance to `msg.sender` (not the original depositor), any third party can immediately steal the stranded ETH.

---

### Finding Description

`PeripheryPayments.pay()` branches on the token address:

```
if (payer == address(this)) { ERC20 transfer }
else if (token == WETH)     { use native balance, then safeTransferFrom }
else                        { safeTransferFrom(payer, recipient, value) }  // ← ETH ignored
``` [1](#0-0) 

When `token` is any ERC20 other than WETH, the `else` branch runs and `msg.value` ETH already sitting on the router is never touched. The swap succeeds, the ERC20 is pulled from the user, and the ETH remains on the contract.

All four public swap functions are `payable` and impose no guard requiring `msg.value == 0` when `tokenIn != WETH`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does **not** protect against this: it only fires for plain ETH transfers with no calldata, not for ETH sent alongside a function call. [6](#0-5) 

The stranded ETH is then immediately claimable by any caller via `refundETH()`, which sends the full router balance to `msg.sender`:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← sends to caller, not original depositor
    }
}
``` [7](#0-6) 

---

### Impact Explanation

**Direct loss of user principal.** A user who mistakenly sends ETH with a non-WETH ERC20 swap loses that ETH permanently to any frontrunner or MEV bot that calls `refundETH()` in the same or a subsequent block. The swap itself succeeds (ERC20 is pulled correctly), so the user receives no error signal. The loss is the full `msg.value` sent.

This is worse than the seed bug (InfinityExchange M-05), where ETH was merely frozen in the contract. Here the ETH is actively stealable by any unprivileged address.

---

### Likelihood Explanation

**Medium.** The same four functions are used for both WETH-as-native-ETH flows and pure ERC20 flows. The Uniswap v3 multicall pattern (send ETH, swap WETH, call `refundETH`) is well-known and users familiar with it may accidentally attach `msg.value` to a non-WETH swap. The interface comment on `IMetricOmmSimpleRouter` even notes "ERC-20 routes only. No native ETH" but this is a NatSpec comment, not an on-chain enforcement. [8](#0-7) 

---

### Recommendation

Add a `msg.value == 0` guard in each swap function when `tokenIn != WETH`, or add a single shared guard at the top of `_justPayCallback` / `pay()`:

```solidity
// In PeripheryPayments.pay(), else branch:
} else {
    if (msg.value != 0) revert NativeETHNotAccepted();
    IERC20(token).safeTransferFrom(payer, recipient, value);
}
```

Alternatively, add a modifier to each swap entry point:

```solidity
modifier noNativeUnlessWETH(address tokenIn) {
    if (tokenIn != WETH && msg.value != 0) revert NativeETHNotAccepted();
    _;
}
```

Apply it to `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Proof of Concept

```solidity
// Setup: pool with token0=WETH, token1=USDC; victim has USDC allowance on router.
// Attacker watches the mempool.

// Step 1 – Victim calls exactInputSingle with accidental msg.value:
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(USDC),   // ← NOT WETH
        tokenOut:        address(WETH),
        zeroForOne:      false,
        amountIn:        1_000e6,
        amountOutMinimum: 0,
        recipient:       victim,
        deadline:        block.timestamp + 60,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// pay() takes the `else` branch → safeTransferFrom(USDC, victim, pool, 1000e6)
// 1 ETH is now stranded on the router.
// Victim receives WETH output; no revert; no indication of loss.

// Step 2 – Attacker (or any address) calls refundETH() in the same block:
router.refundETH();
// _transferETH(msg.sender, 1 ether) → attacker receives victim's 1 ETH.
```

The `pay()` call path for the victim's swap: [9](#0-8) [10](#0-9) 

The theft path: [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L11-11)
```text
/// @dev Scope: ERC-20 routes only. No native ETH, WETH wrap/unwrap, on-chain quotes, sweep, or refund helpers.
```
