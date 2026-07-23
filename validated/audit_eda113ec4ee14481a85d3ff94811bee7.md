### Title
Pool Self-Recipient in `swap` Silently Converts Trader Output Into Swept Protocol Fees — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.swap` accepts an arbitrary `recipient` address with no guard against `recipient == address(this)`. When the pool address is passed as `recipient`, the output-token transfer is a self-transfer (balance unchanged), yet `binTotals` is decremented as if the tokens left. The resulting phantom surplus is later swept as spread fees via `collectFees`, permanently destroying the trader's output.

---

### Finding Description

The swap execution path in `MetricOmmPool.sol` is:

1. `_executeSwap` decrements `binTotals.scaledToken1` (for a `zeroForOne` swap) by the full output amount.
2. `transferToken1(recipient, uint256(-amount1Delta))` is called **before** the callback.
3. The `IncorrectDelta` guard only checks that the **input** token arrived; it never verifies that the output token actually left the pool. [1](#0-0) 

When `recipient == address(this)`, `safeTransfer(address(this), amount)` is a no-op on the pool's ERC-20 balance — `balance1()` is unchanged. But `binTotals.scaledToken1` has already been reduced: [2](#0-1) 

This creates a permanent discrepancy:

```
balance1() * TOKEN_1_SCALE_MULTIPLIER  >  binTotals.scaledToken1 + notionalFeeToken1Scaled
```

`collectFees` treats exactly this gap as the spread-fee surplus and distributes it to the admin and protocol: [3](#0-2) 

The trader paid full token0 input (enforced by the callback check) but received zero token1 output. Their output is silently reclassified as protocol revenue.

There is no `require(recipient != address(this))` anywhere in `swap`: [4](#0-3) 

---

### Impact Explanation

- **Direct loss of user principal**: the trader loses 100 % of their expected output tokens.
- **Pool accounting corruption**: `binTotals` no longer covers actual LP claims for the output token; the phantom surplus inflates the spread-fee pool, so fee recipients receive tokens that belong to the trader.
- **Permanent**: once `collectFees` is called the tokens are transferred out; there is no recovery path.

---

### Likelihood Explanation

- Any caller of `swap` controls `recipient` directly; no router or factory validation prevents `recipient = pool`.
- In multi-hop routing built on top of the pool (e.g., custom integrators), intermediate recipients are computed programmatically and a bug there trivially produces `recipient = pool`.
- The `MetricOmmSimpleRouter` itself passes `address(this)` as intermediate recipient in `exactInput`; a one-off error in a similar router would trigger this silently. [5](#0-4) 

---

### Recommendation

Add a self-recipient guard at the top of `swap`:

```solidity
require(recipient != address(this), SelfRecipient());
```

This mirrors the fix applied to the PSP22Wrapper analog (OpenBrush PR #140) and costs a single comparison.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool(token0=USDC, token1=WETH)
  alice = trader

1. Alice calls pool.swap(
       recipient    = address(pool),   // ← self-address
       zeroForOne   = true,
       amountSpecified = 1_000e6,      // exact-in 1 000 USDC
       priceLimitX64 = 0,
       callbackData  = "",
       extensionData = ""
   );

2. _executeSwap computes:
     amount0DeltaScaled = +1_000e6 * TOKEN_0_SCALE_MULTIPLIER
     amount1DeltaScaled = -X  (e.g. -0.5e18 WETH at market)
   binTotals.scaledToken1 -= X   ← accounting says WETH left

3. transferToken1(address(pool), 0.5e18) → self-transfer, balance1() unchanged.

4. Callback fires; Alice pays 1 000 USDC. IncorrectDelta check passes.

5. State after swap:
     balance1()  * TOKEN_1_SCALE_MULTIPLIER  =  binTotals.scaledToken1 + X
     (surplus = X = Alice's 0.5 WETH)

6. Admin calls factory.collectFees(…).
   surplus1Scaled = X → swept to adminFeeDestination / FACTORY.

Alice lost 1 000 USDC and received 0 WETH.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-225)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L250-263)
```text
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L385-388)
```text
    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L738-739)
```text
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - uint256(-amount1DeltaScaled));
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
