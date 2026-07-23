### Title
Missing Zero-Address Validation on `swap()` `recipient` Allows Accidental Token Burns - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary
The `swap()` function in `MetricOmmPool` accepts a caller-supplied `recipient` address and immediately transfers the output token leg to it before the callback settles the input. No guard prevents `recipient == address(0)`, so a caller who passes the zero address loses the entire swap output while still paying the full input amount.

### Finding Description
`MetricOmmPool.swap()` transfers the output token to `recipient` unconditionally:

```solidity
// zeroForOne path
if (amount1Delta < 0) {
    transferToken1(recipient, uint256(-amount1Delta));   // ← no address(0) check
}
// !zeroForOne path
if (amount0Delta < 0) {
    transferToken0(recipient, uint256(-amount0Delta));   // ← no address(0) check
}
``` [1](#0-0) 

`transferToken0` / `transferToken1` are thin wrappers around `IERC20.safeTransfer`: [2](#0-1) 

A grep across all core contracts confirms there is **no** `address(0)` guard anywhere in the production swap path. The factory validates `adminFeeDestination` and `admin` at pool-creation time, but the swap `recipient` is a per-call parameter with no analogous check. [3](#0-2) 

### Impact Explanation
If `recipient` is `address(0)`:
1. The pool calls `transferToken0(address(0), amount)` or `transferToken1(address(0), amount)`.
2. `safeTransfer` to `address(0)` succeeds on standard ERC-20 tokens (including USDC/USDT), crediting the zero address.
3. The output tokens are permanently burned.
4. The swap callback then pulls the full input amount from the caller.
5. Net result: caller pays full input, receives zero output — complete loss of the swap output principal.

The loss is unbounded (up to the pool's entire liquid reserve for that token leg) and irreversible.

### Likelihood Explanation
- Any contract integrating the pool that leaves a `recipient` variable uninitialized (defaults to `address(0)`) will trigger this silently.
- A periphery router that computes `recipient` from user-supplied calldata and fails to validate it before forwarding to the pool is a realistic integration path.
- The `MetricOmmSimpleRouter` passes `recipient` straight through from user-supplied `ExactInputSingleParams.recipient` with no zero-address guard visible in the periphery layer. [4](#0-3) 

### Recommendation
Add a zero-address guard at the top of `swap()`, mirroring the pattern already used for `adminFeeDestination` in the factory:

```solidity
function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(recipient != address(0), ZeroRecipient());   // ← add this
    require(amountSpecified != 0, InvalidAmount());
    ...
}
```

The same guard should be applied to `addLiquidity()` for the `owner` parameter, since LP shares minted to `address(0)` are also permanently lost.

### Proof of Concept
```solidity
// Attacker / misconfigured integrator calls swap with address(0) recipient
pool.swap(
    address(0),   // recipient — zero address, no revert
    true,         // zeroForOne
    int128(1000), // exact input of 1000 token0
    0,            // no price limit
    callbackData,
    ""
);
// Pool transfers token1 output to address(0) → burned
// Callback pulls 1000 token0 from msg.sender → paid
// Net: caller loses 1000 token0 AND receives 0 token1
``` [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-278)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L565-571)
```text
  function transferToken0(address to, uint256 amount) internal {
    IERC20(TOKEN0).safeTransfer(to, amount);
  }

  function transferToken1(address to, uint256 amount) internal {
    IERC20(TOKEN1).safeTransfer(to, amount);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L548-554)
```text
  function _validatePoolParameters(PoolParameters calldata params) internal view {
    if (params.token0 == address(0) || params.token1 == address(0) || params.token0 == params.token1) {
      revert InvalidTokenConfig();
    }
    if (params.admin == address(0)) revert InvalidAdmin();
    _validatePriceProvider(params.token0, params.token1, params.priceProvider);
    if (params.adminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
```
