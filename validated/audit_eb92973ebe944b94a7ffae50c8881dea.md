### Title
JIT Liquidity Attack Drains LP Spread Fees from Existing Liquidity Providers — (File: `metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

The Metric OMM pool distributes LP spread fees by directly crediting them into bin token balances during swap execution (`SwapMath.sol`). Because `addLiquidity` and `removeLiquidity` carry no block-number guard, time-lock, or same-block restriction, an attacker can front-run a large pending swap by minting a dominant share position in the active bin, capture the majority of the LP fee when the swap settles, and immediately burn their position — stealing fee income that should have accrued to existing LPs.

---

### Finding Description

**Fee flow into bin balances**

In every swap-in-bin function in `SwapMath.sol`, the LP fee (total fee minus the protocol portion) is credited directly into `binState.token*BalanceScaled`:

```solidity
// buyToken0InBinSpecifiedOut (SwapMath.sol ~L413-415)
binState.token0BalanceScaled -= amountOutScaled.toUint104();
binState.token1BalanceScaled =
  (uint256(binState.token1BalanceScaled) + amountInScaled - protocolFeeAmountScaled).toUint104();
// binLpFeeAmount = feeAmountScaled - protocolFeeAmountScaled  (stays in bin balance)
``` [1](#0-0) 

The LP fee is therefore embedded in `binState.token1BalanceScaled` (or `token0BalanceScaled` for the other direction) immediately after the swap step.

**Removal is proportional to current shares**

`LiquidityLib.removeLiquidity` computes each LP's entitlement as a simple ratio of their shares to the total at the moment of removal:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [2](#0-1) 

Whoever holds shares at the time of removal receives their proportional cut of the bin balance — including any LP fees that accrued during the swap.

**No time-lock or block-number guard on liquidity operations**

`addLiquidity` carries only a reentrancy guard and optional extension hooks; there is no `block.number` or `block.timestamp` check anywhere in `metric-core/contracts/**/*.sol` for liquidity operations. The `whenNotPaused` modifier is applied only to `swap`, not to `addLiquidity` or `removeLiquidity`:

```solidity
function addLiquidity(...) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) { ... }
function removeLiquidity(...) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) { ... }
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (...) { ... }
``` [3](#0-2) 

The optional `DepositAllowlistExtension` can gate deposits per pool, but it is not deployed by default and is entirely opt-in: [4](#0-3) 

---

### Impact Explanation

Existing LPs suffer a direct, quantifiable loss of LP fee income. For every large swap, a JIT attacker can capture an arbitrarily large fraction of the LP spread fee by transiently holding a dominant share position. The attacker deposits tokens at the oracle mid-price (no price risk within a single block), collects the fee, and withdraws — net cost is only gas. Existing LPs receive proportionally less than they are owed for providing continuous liquidity.

This matches the allowed impact gate: **direct loss of owed LP assets** (LP fee income is the primary economic incentive for liquidity provision).

---

### Likelihood Explanation

- The attack is **permissionless**: any address can call `addLiquidity` and `removeLiquidity`.
- It is **profitable whenever the LP fee on a single swap exceeds gas cost**, which is routine for swaps of meaningful size.
- MEV bots routinely monitor mempools for large pending swaps and can execute the three-transaction bundle (add → swap executes → remove) atomically via a flashbots bundle or equivalent.
- No special privileges, malicious setup, or non-standard tokens are required.

---

### Recommendation

Implement a JIT liquidity guard at the core pool level. Options include:

1. **Block-number lock**: Record `lastAddBlock[posKey]` on every `addLiquidity` and revert `removeLiquidity` if `block.number == lastAddBlock[posKey]`.
2. **Time-weighted fee accrual**: Instead of crediting LP fees directly into bin balances, accumulate them in a separate per-bin fee accumulator and distribute them based on time-weighted share ownership (similar to Uniswap v3's `feeGrowthInside`).
3. **Minimum hold period extension**: Expose a configurable `minHoldBlocks` parameter via the extension system (`beforeRemoveLiquidity`) so pool admins can enforce a minimum liquidity duration.

Option 1 is the lowest-complexity fix and directly closes the same-block attack vector.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Pseudocode — adapt to MetricOmmPoolBaseTest harness

contract JITAttackPoC {
    IMetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;

    function attack(uint256 victimSwapAmount) external {
        // Step 1: Front-run — add large liquidity to the active bin (bin 0)
        // Attacker mints N shares; existing LPs hold M shares total.
        // Attacker's share fraction ≈ N / (N + M) → ~99% for large N.
        int256[] memory bins = new int256[](1);
        bins[0] = 0; // active bin
        uint256[] memory shares = new uint256[](1);
        shares[0] = 1_000_000e18; // dominate the bin
        pool.addLiquidity(
            address(this), 0,
            LiquidityDelta({binIdxs: bins, shares: shares}),
            "", ""
        );

        // Step 2: Victim's swap executes (in the same block, next tx).
        // LP fee = (bidAskSpread / 2) * victimSwapAmount * (1 - protocolFeeShare)
        // This fee is credited into binState.token1BalanceScaled.

        // Step 3: Back-run — remove all attacker shares immediately.
        pool.removeLiquidity(
            address(this), 0,
            LiquidityDelta({binIdxs: bins, shares: shares}),
            ""
        );
        // Attacker receives: original deposit + ~99% of LP fee from victim's swap.
        // Existing LPs receive: ~1% of LP fee they were entitled to.
    }

    function metricOmmModifyLiquidityCallback(
        uint256 amount0, uint256 amount1, bytes calldata
    ) external {
        if (amount0 > 0) token0.transfer(msg.sender, amount0);
        if (amount1 > 0) token1.transfer(msg.sender, amount1);
    }
}
```

**Concrete numbers** (illustrative):
- Existing LPs: 10,000 shares in bin 0
- Attacker mints: 990,000 shares → holds 99% of bin
- Victim swaps 100,000 token1 → token0; LP fee = 500 token1 (0.5% spread)
- Attacker withdraws: receives 495 token1 in LP fees
- Existing LPs receive: 5 token1 instead of 500 token1
- **Loss to existing LPs: 495 token1 per swap**

The root cause is in `LiquidityLib.addLiquidity` / `LiquidityLib.removeLiquidity` (no time guard) combined with the fee-into-balance accounting in `SwapMath` bin functions. [5](#0-4) [6](#0-5) [1](#0-0) [3](#0-2)

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L409-427)
```text
      uint256 feeAmountScaled = Math.ceilDiv(amountInScaled * currBinBuyFeeX64, ONE_X64);
      amountInScaled += feeAmountScaled;
      uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;

      binState.token0BalanceScaled -= amountOutScaled.toUint104();
      binState.token1BalanceScaled =
        (uint256(binState.token1BalanceScaled) + amountInScaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= amountOutScaled;
      state.amountCalculatedScaled += amountInScaled;
      state.protocolFeeAmountScaled += protocolFeeAmountScaled;

      // casting to int256 is safe because amountOutScaled is bounded by uint104 bin liquidity.
      // forge-lint: disable-next-line(unsafe-typecast)
      delta0Scaled = -int256(amountOutScaled);
      // casting to int256 is safe because amountInScaled - protocolFeeAmountScaled is non-negative and bounded by uint104-scaled bin math.
      // forge-lint: disable-next-line(unsafe-typecast)
      delta1Scaled = int256(amountInScaled - protocolFeeAmountScaled);
      binLpFeeAmount = feeAmountScaled - protocolFeeAmountScaled;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L40-159)
```text
  function addLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Added, uint256 amount1Added) {
    unchecked {
      uint256 length = deltas.binIdxs.length;
      if (length == 0) return (0, 0);

      uint256 totalToken0ToAddScaled = 0;
      uint256 totalToken1ToAddScaled = 0;

      BinBalanceDelta[] memory binBalanceDeltas = new BinBalanceDelta[](length);

      int256 curBinIdxCache = ctx.curBinIdx;

      for (uint256 i = 0; i < length; i++) {
        int256 binIdx = deltas.binIdxs[i];
        uint256 sharesToAdd = deltas.shares[i];

        if (binIdx < ctx.lowestBin || binIdx > ctx.highestBin) revert IMetricOmmPoolActions.InvalidBinIndex(binIdx);
        if (sharesToAdd == 0) continue;

        {
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

          BinState storage binState = binStates[binIdx];

          uint256 amount0Scaled = 0;
          uint256 amount1Scaled = 0;
          if (binTotalSharesVal == 0) {
            if (binIdx < curBinIdxCache) {
              amount1Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken1PerShareE18, sharesToAdd), 1e18);
            } else if (binIdx > curBinIdxCache) {
              amount0Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken0PerShareE18, sharesToAdd), 1e18);
            } else {
              uint256 token0Proportion = type(uint104).max - ctx.curPosInBin;
              uint256 token1Proportion = ctx.curPosInBin;
              amount0Scaled =
              (Math.mulDiv(
                  token0Proportion * ctx.initialScaledToken0PerShareE18,
                  sharesToAdd,
                  uint256(type(uint104).max) * 1e18,
                  Math.Rounding.Ceil
                ));
              amount1Scaled =
              (Math.mulDiv(
                  token1Proportion * ctx.initialScaledToken1PerShareE18,
                  sharesToAdd,
                  uint256(type(uint104).max) * 1e18,
                  Math.Rounding.Ceil
                ));
            }
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
          if (amount0Scaled > 0) {
            totalToken0ToAddScaled += amount0Scaled;
            binState.token0BalanceScaled = (uint256(binState.token0BalanceScaled) + amount0Scaled).toUint104();
          }
          if (amount1Scaled > 0) {
            totalToken1ToAddScaled += amount1Scaled;
            binState.token1BalanceScaled = (uint256(binState.token1BalanceScaled) + amount1Scaled).toUint104();
          }
          binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
          positionBinShares[posKey] = newUserShares;

          binBalanceDeltas[i] = BinBalanceDelta({
            // Safe: per-bin deltas are bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: int256(amount0Scaled),
            // casting to int256 is safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToAddScaled > 0) {
        binTotals.scaledToken0 = (uint256(binTotals.scaledToken0) + totalToken0ToAddScaled).toUint128();
      }
      if (totalToken1ToAddScaled > 0) {
        binTotals.scaledToken1 = (uint256(binTotals.scaledToken1) + totalToken1ToAddScaled).toUint128();
      }

      (amount0Added, amount1Added) =
        _deltasScaledToExternal(totalToken0ToAddScaled, totalToken1ToAddScaled, ctx, Math.Rounding.Ceil);

      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }

      emit IMetricOmmPoolActions.LiquidityAdded(owner, salt, deltas.binIdxs, binBalanceDeltas, deltas.shares);
    }
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-251)
```text
  function removeLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Removed, uint256 amount1Removed) {
    unchecked {
      uint256 length = deltas.binIdxs.length;
      if (length == 0) return (0, 0);

      uint256 totalToken0ToRemoveScaled = 0;
      uint256 totalToken1ToRemoveScaled = 0;

      BinBalanceDelta[] memory binBalanceDeltas = new BinBalanceDelta[](length);

      for (uint256 i = 0; i < length; i++) {
        int256 binIdx = deltas.binIdxs[i];
        uint256 sharesToRemove = deltas.shares[i];

        if (binIdx < ctx.lowestBin || binIdx > ctx.highestBin) {
          revert IMetricOmmPoolActions.InvalidBinIndex(binIdx);
        }
        if (sharesToRemove == 0) continue;

        {
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

          BinState storage binState = binStates[binIdx];
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;

          totalToken0ToRemoveScaled += amount0Scaled;
          totalToken1ToRemoveScaled += amount1Scaled;

          binBalanceDeltas[i] = BinBalanceDelta({
            // safe because amount0Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: -int256(amount0Scaled),
            // safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: -int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - totalToken0ToRemoveScaled);
      }
      if (totalToken1ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - totalToken1ToRemoveScaled);
      }

      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }

      emit IMetricOmmPoolActions.LiquidityRemoved(owner, salt, deltas.binIdxs, binBalanceDeltas, deltas.shares);
    }
  }
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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
