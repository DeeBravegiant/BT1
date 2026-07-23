Audit Report

## Title
Stranded ETH from Payable Non-WETH Calls Is Capturable by Any Subsequent WETH Swap Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
Every user-facing entry point in `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` is marked `payable`, allowing ETH to be sent alongside any call regardless of whether the input token is WETH. The `receive()` guard in `PeripheryPayments` only blocks plain ETH transfers (no calldata), not ETH sent via `payable` function calls. ETH stranded this way is silently consumed by `pay()` on the next WETH-input call, giving that caller a free swap funded by the victim's ETH.

## Finding Description
`PeripheryPayments.receive()` reverts for any sender that is not WETH:

```solidity
// PeripheryPayments.sol L32-34
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
```

This guard applies only to plain ETH transfers (zero-calldata calls). It does **not** apply when ETH is sent alongside a `payable` function call such as `exactInputSingle{value: 1 ether}(...)`. In that case, the EVM routes execution to the function, not to `receive()`, so the ETH is accepted silently.

All four swap entry points are `payable` with no check that `msg.value == 0` when `tokenIn != WETH`:

```solidity
// MetricOmmSimpleRouter.sol L67, 92, 130, 154
function exactInputSingle(...) external payable ...
function exactInput(...) external payable ...
function exactOutputSingle(...) external payable ...
function exactOutput(...) external payable ...
```

All four liquidity entry points are similarly `payable` with no such guard:

```solidity
// MetricOmmPoolLiquidityAdder.sol L56, 71, 88, 123
function addLiquidityExactShares(...) external payable ...
function addLiquidityWeighted(...) external payable ...
```

When the pool's swap callback fires and `_justPayCallback` calls `pay(WETH, payer, pool, value)`, the WETH branch in `pay()` checks `address(this).balance` first:

```solidity
// PeripheryPayments.sol L73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer bypassed entirely
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

If `address(this).balance >= value`, the contract wraps its own ETH and forwards it to the pool. The `payer` (the attacker) is never charged via `transferFrom`. Any ETH previously stranded by a victim's non-WETH call is consumed as the attacker's input.

`refundETH()` exists but is a separate, optional call that does not protect against front-running between the victim's transaction and the attacker's.

## Impact Explanation
Direct loss of user ETH principal. A victim who accidentally sends ETH with a non-WETH swap or liquidity call loses that ETH permanently unless they include `refundETH()` in the same multicall batch. An attacker who observes stranded ETH on-chain can immediately call `exactInputSingle` with `tokenIn = WETH` and `amountIn ≤ stranded ETH`, receiving the full swap output at zero cost. The same theft path applies inside `MetricOmmPoolLiquidityAdder` for WETH-leg liquidity adds. This constitutes a direct loss of user principal, qualifying as High impact under Sherlock thresholds.

## Likelihood Explanation
Low. The attack requires a user error: sending ETH alongside a non-WETH call. This is a realistic mistake because all entry points are `payable` and the WETH-via-ETH pattern is explicitly supported and documented. Once ETH appears stranded on-chain, front-running it is trivial and requires no special privileges. The attack is repeatable for every such user error.

## Recommendation
1. **Primary fix**: Add `if (msg.value > 0 && tokenIn != WETH) revert ETHNotAccepted();` at the top of each swap and liquidity function that does not use native ETH.
2. **Alternative**: Remove `payable` from functions that have no WETH leg. For functions that may or may not use ETH, enforce `msg.value == 0` when the relevant token is not WETH.
3. **Defense-in-depth**: In `pay()`, if `address(this).balance > 0` and `token != WETH`, revert or emit an event to surface unexpected ETH accumulation.

## Proof of Concept
```
Setup:
  - Pool A: token0 = USDC, token1 = DAI  (no WETH leg)
  - Pool B: token0 = WETH, token1 = DAI

Step 1 — Victim:
  victim calls exactInputSingle{value: 1 ether}(
      pool = Pool_A, tokenIn = USDC, tokenOut = DAI, amountIn = 1000e6, ...
  )
  → receive() is NOT invoked (payable function call, not plain ETH transfer)
  → swap executes normally using USDC transferFrom
  → 1 ETH remains in MetricOmmSimpleRouter (not returned, no revert)

Step 2 — Attacker (same block or next):
  attacker calls exactInputSingle{value: 0}(
      pool = Pool_B, tokenIn = WETH, tokenOut = DAI, amountIn = 1e18, ...
  )
  → pool calls metricOmmSwapCallback
  → _justPayCallback calls pay(WETH, attacker, pool, 1e18)
  → address(this).balance == 1e18 >= 1e18
  → contract wraps victim's 1 ETH → 1 WETH → transfers to pool
  → attacker receives DAI output; attacker spent 0 ETH and 0 WETH

Result:
  Victim lost 1 ETH.
  Attacker gained DAI equivalent of 1 WETH at zero cost.
```