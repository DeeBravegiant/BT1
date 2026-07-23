Audit Report

## Title
Reverting Extension Permanently Locks LP Funds in `removeLiquidity` With No Emergency Exit - (File: `metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/ExtensionCalling.sol`, `metric-core/contracts/libraries/CallExtension.sol`)

## Summary

`removeLiquidity` unconditionally invokes `_beforeRemoveLiquidity` and `_afterRemoveLiquidity` extension hooks, and `CallExtension.callExtension` propagates every revert from an extension directly to the caller with no try/catch. Extensions are stored as pool-level immutables set at construction and cannot be replaced. `BaseMetricExtension` defaults to `revert ExtensionNotImplemented()` for both remove-liquidity hooks. Because `createPool` is permissionless and `ValidateExtensionsConfig` does not verify that registered extensions actually implement the required hooks, any pool deployed with a reverting extension on either remove-liquidity order permanently freezes all LP principal with no recovery path.

## Finding Description

**Code path — `removeLiquidity` in `MetricOmmPool.sol`:**

`_beforeRemoveLiquidity` and `_afterRemoveLiquidity` are called unconditionally around `LiquidityLib.removeLiquidity`:

```solidity
_beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);   // line 207
(amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);     // lines 208-210
_afterRemoveLiquidity(msg.sender, owner, salt, deltas, ...);              // line 211
```

Both delegate to `_callExtensionsInOrder` in `ExtensionCalling.sol`, which iterates the packed order and calls each registered extension via `CallExtension.callExtension`. There is no `try/catch`, no skip-on-failure, and no fallback — every revert from the extension bubbles directly to the caller:

```solidity
(bool success, bytes memory result) = extension.call(data);
if (!success) {
    if (result.length > 0) { assembly { revert(...) } }
    revert ExtensionCallFailed();
}
if (result.length < 32) { revert InvalidExtensionResponse(); }
```

A self-destructed extension returns empty data, triggering `InvalidExtensionResponse` on the `result.length < 32` check.

**Extensions are immutable.** All seven slots (`EXTENSION_1`–`EXTENSION_7`) and all six order words (`BEFORE_REMOVE_LIQUIDITY_ORDER`, `AFTER_REMOVE_LIQUIDITY_ORDER`, etc.) are Solidity `immutable` variables set in the `ExtensionCalling` constructor and cannot be updated post-deployment.

**`BaseMetricExtension` reverts by default.** Any extension inheriting `BaseMetricExtension` that does not override `beforeRemoveLiquidity` or `afterRemoveLiquidity` will always revert with `ExtensionNotImplemented()`.

**`ValidateExtensionsConfig` does not check hook implementation.** The factory's validation only checks: extension count ≤ 7, no zero addresses, no duplicates, and that order indices reference valid slots. It does not call or simulate the extension hooks, so a `BaseMetricExtension` subclass that omits the override passes all factory checks and deploys successfully.

**`createPool` is permissionless.** The interface explicitly documents: "`createPool` is permissionless once `poolDeployer` is set (not `onlyOwner`)." Any public caller can deploy a pool with any extension configuration that passes the structural (not behavioral) validation.

**No emergency exit exists.** The pause mechanism only gates `swap` via `whenNotPaused`; `removeLiquidity` has no pause guard and no alternative withdrawal path. The factory has no function to replace an extension on a deployed pool.

## Impact Explanation

Any pool deployed with an extension registered for `BEFORE_REMOVE_LIQUIDITY_ORDER` or `AFTER_REMOVE_LIQUIDITY_ORDER` that reverts will have all LP principal permanently frozen. Users cannot call `removeLiquidity` because every call reverts at the extension hook. There is no alternative withdrawal path. This is a direct, total loss of user principal for all LPs in the affected pool — matching the allowed impact gate: "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."

## Likelihood Explanation

The trigger does not require a malicious actor. The most realistic scenario is accidental misconfiguration: a developer writes an extension inheriting `BaseMetricExtension`, registers it for `beforeRemoveLiquidity` or `afterRemoveLiquidity`, but forgets to override those functions. The base class reverts with `ExtensionNotImplemented()` on every call, immediately and permanently locking all LP funds. Because `createPool` is permissionless and `ValidateExtensionsConfig` performs no behavioral check on extensions, this misconfiguration deploys successfully and is undetectable on-chain until an LP attempts withdrawal. A production extension bug (e.g., a revert under certain market conditions) produces the same outcome with no recovery path.

## Recommendation

1. **Add an emergency withdrawal path** that skips extension hooks — e.g., a factory-callable `emergencyRemoveLiquidity` that bypasses `_beforeRemoveLiquidity` / `_afterRemoveLiquidity`, or a pool-level flag set by the factory that disables extension calls for the remove-liquidity path only.
2. **Wrap remove-liquidity extension calls in `try/catch`** so a reverting extension degrades gracefully (emitting an event) rather than permanently locking funds.
3. **Change `BaseMetricExtension` defaults** for `beforeRemoveLiquidity` and `afterRemoveLiquidity` to return the correct selector (pass-through) rather than reverting, so unoverridden hooks do not silently brick withdrawals.

## Proof of Concept

```solidity
// 1. Deploy a pool (permissionless via createPool) with an extension
//    inheriting BaseMetricExtension registered for BEFORE_REMOVE_LIQUIDITY_ORDER,
//    without overriding beforeRemoveLiquidity().
//    ValidateExtensionsConfig passes — no behavioral check is performed.

// 2. LP calls addLiquidity() successfully.
//    (addLiquidity calls _beforeAddLiquidity, not _beforeRemoveLiquidity.)

// 3. LP calls removeLiquidity():
//    -> _beforeRemoveLiquidity() is called
//    -> _callExtensionsInOrder(BEFORE_REMOVE_LIQUIDITY_ORDER, ...)
//    -> CallExtension.callExtension(extension, data)
//    -> extension.beforeRemoveLiquidity(...) reverts ExtensionNotImplemented()
//    -> revert bubbles through callExtension (lines 10-16 of CallExtension.sol)
//    -> removeLiquidity() reverts
//    -> LP funds are permanently locked; no emergency exit exists.

// The test suite confirms revert-bubbling is the intended behavior
// (test_simulateSwap_beforeSwapRevertBubblesToCaller in MetricOmmPool.extensions.t.sol),
// and the same CallExtension.callExtension is used for all hook types
// including remove-liquidity — confirming the path is live and unguarded.
```