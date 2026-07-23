Audit Report

## Title
LP Fee Sandwich: Attacker Captures Existing LPs' Swap Fees by Sandwiching Swaps with Proportional Liquidity Add/Remove — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
LP fees are deposited directly into each bin's token balance during a swap via `SwapMath`, making them immediately claimable by any current shareholder. Because `addLiquidity` and `removeLiquidity` both price shares proportionally to the **current** bin balance with no time-lock or fee-on-entry guard, an attacker can sandwich any swap: add a dominant share position before the swap, let the LP fee land in the bin, then immediately remove and extract the fee. Existing LPs lose the fees they earned.

## Finding Description

**LP fee lands directly in bin balance during swap.**

In `SwapMath.buyToken1InBinSpecifiedIn` (and the symmetric direction), the LP fee is the gross input minus the protocol-fee portion, and it is written directly into `binState.token0BalanceScaled`:

```solidity
// SwapMath.sol L779-781
binState.token1BalanceScaled -= out1Scaled.toUint104();
binState.token0BalanceScaled =
  (uint256(binState.token0BalanceScaled) + totalIn0Scaled - protocolFeeAmountScaled).toUint104();
``` [1](#0-0) 

The LP fee (`token0FeeScaled - protocolFeeAmountScaled`) is therefore immediately part of the bin balance, claimable by whoever holds shares at removal time. [2](#0-1) 

**`addLiquidity` prices new shares at the current (post-fee) bin balance.**

When a bin already has shares (`binTotalSharesVal != 0`), `LiquidityLib.addLiquidity` computes the required deposit proportionally to the current balance:

```solidity
// LiquidityLib.sol L109-110
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
``` [3](#0-2) 

**`removeLiquidity` returns tokens proportionally to the current bin balance at removal time.**

```solidity
// LiquidityLib.sol L205-206
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [4](#0-3) 

**No guards exist in the core pool.**

`MetricOmmPool.addLiquidity` and `removeLiquidity` carry only a `nonReentrant` guard (preventing reentrancy within a single call) and an `msg.sender != owner` check on removal (which the attacker trivially satisfies by being their own owner). The `_beforeAddLiquidity` / `_beforeRemoveLiquidity` hooks delegate to optional extensions; the base pool deploys with all extension slots set to `address(0)`, meaning no time-lock or fee-on-entry is enforced at the protocol level. [5](#0-4) [6](#0-5) 

**Exploit flow:**

Let bin X have balance `B` token0 and `T` total shares (held by honest LPs). Attacker:
1. **Front-runs** the swap: calls `addLiquidity` with `S >> T` shares, paying `B * S / T` token0 (proportional to pre-fee balance).
2. Swap executes; LP fee `F` token0 enters the bin. Bin balance becomes `B + F`, total shares `T + S`.
3. **Back-runs** with `removeLiquidity` for `S` shares, receiving `(B + F) * S / (T + S)` token0.

Attacker profit = `(B + F) * S / (T + S) − B * S / T = F * S * T / (T * (T + S)) ≈ F` when `S >> T`.

Honest LPs receive only `(B + F) * T / (T + S) ≈ 0` of the fee instead of the full `F` they earned.

## Impact Explanation

Existing LP providers suffer a direct, repeatable loss of earned swap fees. With a flash loan, the attacker can make `S` arbitrarily large relative to `T`, capturing essentially 100% of the LP fee from any swap. This makes LP provision economically unviable and constitutes a direct loss of owed LP assets — matching the "Critical/High direct loss of user principal or owed LP assets" criterion.

## Likelihood Explanation

Any swap generating a non-zero LP fee (i.e., `spreadFeeE6 < 1e6` so the LP portion is positive) is exploitable. The attacker requires only: MEV capability to front-run/back-run in the same block (standard on EVM chains), a flash loan source for capital (widely available), and no special permissions — `addLiquidity` is fully permissionless. The attack is repeatable on every swap in every bin.

## Recommendation

Implement one or more of the following mitigations:

1. **Fee-on-entry**: When adding liquidity to a non-empty bin, charge the new LP a proportional share of accrued LP fees (i.e., the excess of current bin balance over the "principal" balance). This prevents entering at the pre-fee price and exiting at the post-fee price.
2. **Withdrawal delay / time-lock**: Require a minimum holding period (e.g., one block) between `addLiquidity` and `removeLiquidity` for the same position, preventing same-block sandwich attacks.
3. **Snapshot-based fee accounting**: Track a per-share fee accumulator (similar to Uniswap v3's `feeGrowthInside`) so fees are attributed only to shares that existed at the time of the swap.

## Proof of Concept

```
State: Bin 0, token0 balance = 10,000, totalShares = 1,000 (honest LP)
LP fee per swap ≈ 100 token0 (1% spread, no protocol cut)

Step 1 — Attacker front-runs swap:
  addLiquidity(bin=0, shares=99,000)
  Cost = 10,000 * 99,000 / 1,000 = 990,000 token0
  New state: balance=1,000,000, totalShares=100,000

Step 2 — Swap executes:
  LP fee = 100 token0 → bin balance = 1,000,100
  (SwapMath.sol L780-781: binState.token0BalanceScaled += totalIn0Scaled - protocolFeeAmountScaled)

Step 3 — Attacker back-runs:
  removeLiquidity(bin=0, shares=99,000)
  Receives = 1,000,100 * 99,000 / 100,000 = 990,099 token0
  (LiquidityLib.sol L205: amount0Scaled = token0BalanceScaled * sharesToRemove / binTotalSharesVal)

Attacker profit = 990,099 − 990,000 = 99 token0 (99% of the 100 token0 LP fee)
Honest LP receives = 1,000,100 * 1,000 / 100,000 = 10,001 token0 (only 1 token0 of the fee)
```

Foundry test plan: deploy a pool with `spreadFeeE6 = 0` (all fee goes to LP), seed bin 0 with honest LP shares, execute the three-step sandwich in a single test function (no flash loan needed for the PoC — just use a large `sharesToAdd`), assert attacker profit ≈ 99% of the LP fee and honest LP receives ≈ 1%.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L775-789)
```text
      uint256 token0FeeScaled = lpFeeScaledFromGrossInput(totalIn0Scaled, currBinSellFeeX64, onePlusSellFeeX64);

      uint256 protocolFeeAmountScaled = (token0FeeScaled * spreadFeeE6) / 1e6;

      binState.token1BalanceScaled -= out1Scaled.toUint104();
      binState.token0BalanceScaled =
        (uint256(binState.token0BalanceScaled) + totalIn0Scaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= totalIn0Scaled;
      state.amountCalculatedScaled += out1Scaled;
      state.protocolFeeAmountScaled += protocolFeeAmountScaled;

      delta0Scaled = (totalIn0Scaled - protocolFeeAmountScaled).toInt256();
      delta1Scaled = -out1Scaled.toInt256();
      binLpFeeAmount = token0FeeScaled - protocolFeeAmountScaled;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-111)
```text
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L204-206)
```text
          BinState storage binState = binStates[binIdx];
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-212)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }

  /// @inheritdoc IMetricOmmPoolActions
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```
