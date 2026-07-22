### Title
SwapAllowlistExtension Checks the Router Address Instead of the Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the extension checks the **router's address** rather than the **user's address**. If the pool admin adds the router to the allowlist (a natural step to enable router-based swaps), every user — including those not individually allowlisted — can bypass the restriction by routing through the periphery router.

This is structurally inconsistent with `DepositAllowlistExtension`, which correctly checks the `owner` parameter (the actual position owner), making the deposit allowlist work correctly through `MetricOmmPoolLiquidityAdder` while the swap allowlist does not.

---

### Finding Description

**Pool → Extension actor binding for swaps:**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that first argument (`sender`) against the per-pool allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

So `msg.sender` inside `pool.swap` is the **router**, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

**Contrast with the deposit allowlist (correct behavior):**

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter (second argument), not `sender` (first argument): [4](#0-3) 

The pool passes the explicit `owner` argument through to the extension: [5](#0-4) 

`MetricOmmPoolLiquidityAdder` forwards the caller-supplied `owner` to `pool.addLiquidity`: [6](#0-5) 

So the deposit allowlist correctly identifies the actual position owner regardless of which periphery contract calls `addLiquidity`. The swap allowlist has no equivalent: it sees only the direct caller of `pool.swap`, which is the router.

**The two broken configurations this creates:**

| Router in allowlist? | Result |
|---|---|
| Yes | Any user bypasses the allowlist by routing through `MetricOmmSimpleRouter` |
| No | Individually allowlisted users cannot use the router at all (DoS on the supported periphery path) |

Neither configuration achieves the intended policy of "only allowlisted users may swap, including via the router."

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only) that deploys `SwapAllowlistExtension` to restrict trading to specific counterparties cannot enforce that restriction when `MetricOmmSimpleRouter` is in scope. If the router is added to the allowlist to support the standard periphery path, every address — including those explicitly excluded — can trade at oracle prices in the pool. This constitutes an admin-boundary break: the pool admin's access-control policy is bypassed by an unprivileged path through the supported periphery router.

---

### Likelihood Explanation

Medium. The scenario requires a pool configured with `SwapAllowlistExtension` and the router added to the allowlist. Adding the router is the natural step any pool admin would take when they want allowlisted users to be able to use the standard router. The bypass is then available to every address with no special privileges.

---

### Recommendation

**Short term:** Pass the actual initiating user through the swap path. One approach: the pool could accept an optional `swapper` hint in `extensionData` that the router authenticates (e.g., via a signed permit or by storing `msg.sender` in transient storage before calling the pool, similar to how the router already stores the payer). The extension would then check that authenticated address instead of `sender`.

**Long term:** Adopt a consistent actor-identification model across all extension hooks. The deposit allowlist correctly uses `owner` (the economically attributed party); the swap allowlist should use an equivalent authenticated initiator, not the raw `msg.sender` of `pool.swap`. Consider documenting the actor semantics for each hook in the extension interface so extension authors cannot accidentally check the wrong address.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow allowlisted users to swap via the router.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient, ...).
6. Pool calls extension.beforeSwap(router, recipient, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Attacker's swap executes at oracle price in the curated pool.
   The per-user allowlist is completely bypassed.
``` [7](#0-6) [8](#0-7) [1](#0-0) [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
