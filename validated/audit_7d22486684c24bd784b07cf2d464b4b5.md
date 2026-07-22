### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `sender` = the router address, not the original user. If the router is allowlisted (a natural admin action to support router-mediated swaps for allowed users), every unprivileged user can bypass the per-user allowlist by calling the pool through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this `sender` and calls the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` = pool and `sender` = whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly, making the pool's `msg.sender` the **router**, not the original user: [4](#0-3) 

So the extension checks `allowedSwapper[pool][router]` — the router's allowlist status — not the original user's. This is the exact "two different checks on different actors" mismatch from H-3: the pool admin intends to gate individual users, but the extension gates the router as a monolithic entity.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economically relevant actor), not `sender` (the payer/operator): [5](#0-4) 

This inconsistency confirms the swap extension is checking the wrong actor.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (to let their approved users trade via the router) inadvertently opens the pool to **all** users. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the allowlisted router, not the unapproved caller. This breaks the core access-control invariant of the allowlist extension and allows unauthorized trading on restricted pools.

### Likelihood Explanation

The scenario requires the admin to have allowlisted the router. This is a natural and expected configuration: without it, even allowlisted users cannot use the router (the extension would reject the router's address). The admin is therefore forced into a false choice: either allowlist the router (opening the pool to everyone) or don't (making the router unusable for approved users). The likelihood of the admin allowlisting the router is high.

### Recommendation

The `SwapAllowlistExtension` should gate on the original user, not the pool's `msg.sender`. Two approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads and checks it. This requires a convention between router and extension.

2. **Mirror `DepositAllowlistExtension`**: Change the pool's `_beforeSwap` to pass an additional `originator` field (e.g., read from transient storage set by the router), and have the extension check that field.

The simplest immediate fix is to have the extension check `sender` only when `msg.sender == pool` (direct call), and require the router to encode the real user in `extensionData` for router-mediated calls.

### Proof of Concept

1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, address(router), true)` — router is allowed so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Pool calls `extension.beforeSwap(router, ...)` — `sender` = router, which is allowlisted.
6. Extension passes. Bob's swap executes on the restricted pool. [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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
