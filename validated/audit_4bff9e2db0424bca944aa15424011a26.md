### Title
No lockup period on LP positions enables zero-risk fee sandwiching of bin swaps — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` and `removeLiquidity` impose **no time-based restriction** on LP positions. LP fees are embedded directly into bin token balances during swap execution. Because Metric OMM is oracle-anchored (no impermanent loss), an attacker can front-run any large swap by depositing into the active bin, capture a proportional share of the LP fee when the swap settles, and immediately withdraw — all in the same block — at effectively zero risk. This dilutes the fee revenue owed to existing, long-term LPs.

---

### Finding Description

**Fee embedding mechanism.** During every swap, the LP fee portion of the input token is added directly to the bin's scaled balance. For example, in `buyToken0InBinSpecifiedIn`:

```
binState.token1BalanceScaled =
    uint256(binState.token1BalanceScaled + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
``` [1](#0-0) 

The LP fee (`totalIn1Scaled - protocolFeeAmountScaled - in1WithoutFeeScaled`) stays in the bin and is immediately claimable by any share-holder proportionally.

**No lockup on positions.** `addLiquidity` and `removeLiquidity` carry only a reentrancy guard and an `msg.sender == owner` check on removal. There is no timestamp, block-number, or epoch restriction anywhere in the core pool: [2](#0-1) 

The `grep` search for `lockup`, `cooldown`, `epoch`, `withdrawDelay`, `depositDelay` returns zero hits in `metric-core`.

**Removal uses proportional bin balance.** When an LP removes shares, they receive:

```
amount0Scaled = binState.token0BalanceScaled * sharesToRemove / binTotalSharesVal;
amount1Scaled = binState.token1BalanceScaled * sharesToRemove / binTotalSharesVal;
``` [3](#0-2) 

Because the bin balance already includes the LP fee from the swap, the attacker's withdrawal captures their diluted share of that fee.

**Oracle-anchored pricing eliminates impermanent loss.** In a traditional AMM, a sandwich LP faces impermanent loss risk that offsets the fee gain. In Metric OMM, prices are set by the external oracle; the pool does not discover prices from reserves. The attacker's token composition after the swap reflects the oracle-fair exchange rate, so there is no adverse selection loss to offset the captured fee.

**Optional extensions do not close the gap.** The only deposit-gating mechanism is the opt-in `DepositAllowlistExtension` (an address allowlist) or a custom `beforeAddLiquidity` hook. Neither is deployed by default, and neither constitutes a lockup period — a whitelisted attacker can still execute the same sandwich. [4](#0-3) 

---

### Impact Explanation

Existing LPs who provide continuous liquidity earn less fee revenue than they are owed. For every large swap, a sandwich LP can dilute the fee pool by depositing immediately before and withdrawing immediately after, capturing a share of the LP fee proportional to their injected capital. The existing LPs' share of the fee is reduced by the same proportion. Over time, rational actors will converge on this strategy, making passive LP provision economically unviable and concentrating fee capture among MEV-capable actors. This is a direct loss of owed LP assets.

---

### Likelihood Explanation

Any actor with mempool visibility (standard on Ethereum mainnet and most EVM chains) can execute this attack. No special privilege, allowlist membership, or protocol knowledge beyond reading public state is required. The attack is profitable whenever the LP fee captured exceeds gas cost, which is true for any swap of meaningful size. The attack is repeatable on every swap.

---

### Recommendation

1. **Epoch-based withdraw buffer**: Require that shares minted in epoch `N` cannot be redeemed until epoch `N+1` (or a fixed block/time delay). This is the approach recommended in the analogous BufferBinaryPool report.
2. **Minimum holding period**: Record the block number at which shares were last minted per position key and reject `removeLiquidity` calls within a configurable `minHoldBlocks` window.
3. **Fee snapshot accumulator**: Separate LP fee accounting from bin balances (e.g., a per-share fee accumulator similar to Uniswap v3's `feeGrowthInside`). New depositors would only accrue fees from the moment of deposit, not retroactively from pre-deposit swaps.

---

### Proof of Concept

**Setup:**
- Pool has bin `0` (active bin) with `T0 = 10,000` token0 scaled, `T1 = 0` token1 scaled, `S = 100,000` total shares.
- Honest LP Alice holds all 100,000 shares.
- A large swap is pending in the mempool: trader sells 5,000 token1 to buy token0. At oracle mid-price 1.0 with 1% spread fee, the LP fee ≈ 50 token1 (net of protocol fee).

**Attack steps (single block):**

1. **Attacker front-runs**: calls `addLiquidity` for bin `0` with `s = 100,000` shares (matching Alice's position). Pays `10,000 * 100,000 / 100,000 = 10,000` token0 (proportional to current bin balance). New totals: `T0 = 20,000`, `S = 200,000`.

2. **Swap executes**: bin loses ~5,000 token0 and gains ~5,050 token1 (5,000 fair value + 50 LP fee). New totals: `T0 ≈ 15,000`, `T1 ≈ 5,050`.

3. **Attacker back-runs**: calls `removeLiquidity` for 100,000 shares. Receives:
   - token0: `15,000 * 100,000 / 200,000 = 7,500`
   - token1: `5,050 * 100,000 / 200,000 = 2,525`
   - Total value at oracle price 1.0: `7,500 + 2,525 = 10,025` token0 equivalent.
   - **Net profit: ~25 token0** (half the LP fee), at zero impermanent loss risk.

4. **Alice's loss**: Alice's 100,000 shares now represent `7,500` token0 and `2,525` token1 = `10,025` token0 equivalent. Without the attacker, Alice would have received the full `~50` token0 LP fee. She received only `~25`.

The attacker captured 50% of the LP fee by providing liquidity for a single block. With larger capital relative to the existing pool, the attacker can capture a proportionally larger share. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L639-641)
```text
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-121)
```text
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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L199-214)
```text
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
