Audit Report

## Title
Excess ETH Permanently Stranded and Stealable via Unrestricted `refundETH()` in Payable Swap Functions — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
All four payable swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) accept native ETH for WETH-leg swaps. The internal `pay()` helper wraps only the exact amount required and silently leaves any surplus in the contract. The public `refundETH()` function sends the entire contract ETH balance to whoever calls it, with no access control, enabling a front-runner to steal the stranded surplus before the original caller can reclaim it.

## Finding Description

**Root cause — `pay()` wraps only `value`, not `address(this).balance`:**

In `PeripheryPayments.sol` L73–77, when `token == WETH` and `nativeBalance >= value`, the function wraps exactly `value` wei and transfers WETH to the pool. The remaining `nativeBalance - value` wei stays in the contract with no accounting and no automatic refund:

```solidity
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
  }
``` [1](#0-0) 

**Trigger path — `exactOutputSingle` with excess ETH:**

`exactOutputSingle` is `payable` and returns after `_clearExpectedCallbackPool()` with no refund step. For an exact-output swap, the caller must send at least `amountInMaximum` ETH; the actual `amountIn` returned by the pool is typically less. The gap `msg.value − amountIn` is left in the router: [2](#0-1) 

**Theft vector — `refundETH()` is unrestricted:**

`refundETH()` is an external function with no access control. It sends `address(this).balance` — the entire ETH balance, including surplus left by a prior caller — to whoever calls it: [3](#0-2) 

**Why `receive()` does not protect against accumulation:**

`receive()` only guards plain ETH transfers (no calldata). ETH attached to a `payable` function call (`msg.value`) bypasses `receive()` entirely and is credited to the contract balance unconditionally: [4](#0-3) 

**Same exposure in `addLiquidityExactShares` / `addLiquidityWeighted`:**

Both liquidity-adder entry points are `payable` and route through the same `pay()` helper. Excess ETH from a WETH-leg liquidity add is equally stranded and griefable: [5](#0-4) 

**Test suite confirms the gap:**

The existing test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` demonstrates the intended multicall + `refundETH` pattern, but no test covers the single-call path where excess ETH is left unrecovered: [6](#0-5) 

## Impact Explanation

A user who calls `exactOutputSingle{value: X}(...)` directly (not via `multicall`) with `X > amountIn` loses `X − amountIn` ETH permanently if a front-runner calls `refundETH()` before the victim does. This is a direct loss of user principal with no protocol-level recovery path. Severity: **Medium** — direct ETH loss of user principal, but requires the user to call the function outside of a `multicall` + `refundETH` bundle.

## Likelihood Explanation

`exactOutputSingle` is a standard, named, `payable` entry point. Nothing in the function signature, NatSpec, or revert messages warns callers that they must batch a `refundETH()` call. Users who interact directly with the ABI (e.g., via Etherscan, a custom script, or a wallet integration that does not construct multicalls) will routinely leave surplus ETH in the contract. MEV bots continuously scan for exactly this pattern. The condition is repeatable and requires no special privilege.

## Recommendation

At the end of each payable swap/liquidity function, automatically refund any remaining native balance to `msg.sender`:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    // ... existing logic ...
    _clearExpectedCallbackPool();
    // Refund unused ETH
    uint256 surplus = address(this).balance;
    if (surplus > 0) _transferETH(msg.sender, surplus);
}
```

Apply the same pattern to `exactInputSingle`, `exactInput`, `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted`. Alternatively, add prominent NatSpec on every `payable` entry point stating that callers **must** batch `refundETH()` in the same `multicall` transaction.

## Proof of Concept

```solidity
// Alice calls exactOutputSingle directly with excess ETH
// Actual amountIn = 1.5 ETH, but Alice sends 2 ETH
uint256 amountIn = router.exactOutputSingle{value: 2 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: 1_000,
        amountInMaximum: 2 ether,
        recipient: alice,
        deadline: block.timestamp + 60,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// amountIn == 1.5 ETH; 0.5 ETH is now stranded in the router

// Bob (front-runner) calls refundETH() in the next block
vm.prank(bob);
router.refundETH();
assertEq(bob.balance, 0.5 ether); // Bob stole Alice's surplus
assertEq(address(router).balance, 0);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
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
