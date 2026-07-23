### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass per-user swap restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the originating EOA. A pool admin who allowlists the router address (the natural choice for "allow router users") inadvertently opens the pool to every user, defeating the per-user restriction entirely.

---

### Finding Description

The call chain for a router-mediated swap is:

```
EOA (Alice) → MetricOmmSimpleRouter.exactInputSingle()
                └─ pool.swap(recipient, ...) [msg.sender = router]
                     └─ _beforeSwap(sender = router, ...)
                          └─ SwapAllowlistExtension.beforeSwap(sender = router, ...)
                               └─ checks allowedSwapper[pool][router]
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` value to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received — the router address when the user went through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same identity substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

**Allowlist bypass (primary impact):** A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every user who calls the router — including users the admin explicitly never allowlisted. The per-user gate is completely ineffective. Unauthorized swappers can trade against LP capital in a pool designed to restrict counterparties, exposing LPs to toxic flow and direct loss of LP principal.

**Allowlist false-block (secondary impact):** A pool admin who allowlists specific EOAs (Alice, Bob) finds those users cannot swap through the router at all, because the router's address is not in the allowlist. The intended flow is broken.

The `generate_scanned_questions.py` audit target explicitly flags this surface:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [6](#0-5) 

---

### Likelihood Explanation

- Any user can call `MetricOmmSimpleRouter` — it is a public, permissionless contract.
- No special setup is required beyond the pool admin having allowlisted the router (the natural configuration for a pool that accepts router traffic).
- The attacker needs zero privileges and zero off-chain coordination.

---

### Recommendation

The `SwapAllowlistExtension` must check the **originating user**, not the intermediary router. Two viable approaches:

1. **Pass the original caller in `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of (or in addition to) `sender`:** For single-hop swaps the recipient is often the user, but this is not reliable for multi-hop paths where intermediate recipients are the router itself.

3. **Expose `msgSender()` on the router:** The router stores the original caller in transient storage (`_getPayer()`). An extension could call `IMetricOmmSimpleRouter(sender).getPayer()` if the sender is a known router — analogous to the Kyber recommendation in the external report. [7](#0-6) 

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists the router so router-mediated swaps are accepted
ext.setAllowedToSwap(pool, address(router), true);

// Eve is NOT individually allowlisted
assertFalse(ext.isAllowedToSwap(pool, eve));

// Eve calls the router — pool.swap() sees msg.sender = router
vm.prank(eve);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: eve,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// ✓ Eve's swap succeeds — allowlist bypassed
// LPs are exposed to Eve's (potentially toxic) flow
```

The `beforeSwap` check at line 37 of `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]`, which is `true`, so the gate passes for Eve even though `allowedSwapper[pool][eve]` is `false`. [3](#0-2)

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L70-76)
```text
  function _getPayer() internal view returns (address payer) {
    return TransientCallbackPool.getPayer();
  }

  function _getTokenToPay() internal view returns (address tokenToPay) {
    return TransientCallbackPool.getTokenToPay();
  }
```
