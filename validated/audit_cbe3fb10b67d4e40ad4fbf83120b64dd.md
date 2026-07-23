Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` validates the router address instead of the end user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router (required for any router-mediated swap) inadvertently grants every user unrestricted swap access, completely defeating the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool see `msg.sender = router`: [3](#0-2) 

The same substitution occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`. The pool admin faces an impossible choice: not allowlisting the router blocks all router users (including legitimate ones), while allowlisting the router grants every user on the network unrestricted swap access. There is no configuration that permits specific users to swap via the router while blocking others.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) can be bypassed by any unprivileged user simply by calling `MetricOmmSimpleRouter` instead of the pool directly. The allowlist gate is rendered completely ineffective for all router-mediated swap paths. Real token balances move: the bypassing user receives output tokens from the pool and the pool receives input tokens, constituting an unauthorized swap against a pool whose admin intended to restrict access. This constitutes a broken core pool access-control mechanism with direct fund movement.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. Any user who discovers the bypass can exploit it immediately without any privileged access. The only precondition is that the pool has `SwapAllowlistExtension` configured and the router is allowlisted — which is a necessary condition for any legitimate router user to swap. The bypass requires no special tokens, no flash loans, and no admin cooperation.

## Recommendation
The extension must validate the end user identity, not the immediate caller. Two complementary approaches:

1. **Pass the original initiator through the router.** Modify `MetricOmmSimpleRouter` to encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router address.

2. **Check both `sender` and a user field in extension data.** Extend the `beforeSwap` interface so the router always forwards the originating user address, and the extension checks that address against the allowlist instead of (or in addition to) `sender`.

Either approach requires the extension to distinguish "router acting on behalf of an allowed user" from "router acting on behalf of a blocked user."

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only allowedUser is on the allowlist.
// The router must be allowlisted for any router swap to work.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// blockedUser is NOT on the allowlist.

// Direct swap by blockedUser → correctly reverts NotAllowedToSwap
vm.prank(blockedUser);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(blockedUser, true, 1000, 0, "", "");

// Router-mediated swap by blockedUser → succeeds, bypassing the allowlist
vm.prank(blockedUser);
token0.approve(address(router), type(uint256).max);
uint256 amountOut = router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: blockedUser,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// blockedUser receives token1 despite not being on the allowlist.
assertGt(amountOut, 0);
```

The pool's `swap` call originates from the router (`msg.sender = router`), so `sender = router` is passed to `beforeSwap`. Since the router is allowlisted, the check at `allowedSwapper[pool][router]` passes and `blockedUser` receives output tokens. [4](#0-3) [5](#0-4)

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
