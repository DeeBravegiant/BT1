### Title
Missing Minimum-Output Slippage Protection on `removeLiquidity` — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.removeLiquidity` accepts only the shares to burn per bin; it provides no way for the LP to specify minimum token amounts to receive. Because the per-share token value is determined by the live bin balances at execution time—which change with every swap—an LP can receive materially less than expected with no on-chain protection.

---

### Finding Description

`removeLiquidity` signature:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,   // only binIdxs + shares
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
``` [1](#0-0) 

The `LiquidityDelta` struct carries only `binIdxs` and `shares`; there are no `minAmount0` / `minAmount1` fields. The tokens returned are computed from the bin's live `token0BalanceScaled` / `token1BalanceScaled` at the moment the transaction executes:

```
amount_out = (shares_burned / binTotalShares) × binTokenBalance
```

`binTokenBalance` changes with every swap that crosses the bin, because swaps update `BinState.token0BalanceScaled` / `token1BalanceScaled` and `binTotals`. Between the time an LP signs the transaction and the time it is mined, any number of swaps can shift the bin composition, reducing the LP's payout.

The periphery provides slippage protection only for the **add** path: `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` and `addLiquidityWeighted` both accept `maxAmountToken0` / `maxAmountToken1` caps enforced in the callback. [2](#0-1) 

No equivalent periphery contract exists for `removeLiquidity`. The only public entry point is the bare pool function, which returns whatever the bins hold at execution time. [3](#0-2) 

---

### Impact Explanation

An LP submits `removeLiquidity` expecting to redeem shares worth, say, $1 000 at the current bin composition. Before the transaction is mined, a large swap drains token0 from the bin and replaces it with token1. The LP receives a token mix worth materially less (or in a ratio they did not want), with no revert path. The loss is permanent and unrecoverable because the LP already burned their shares.

This is a **direct loss of LP principal** with no existing on-chain guard, satisfying the Medium/High impact threshold.

---

### Likelihood Explanation

- Any swap that crosses the LP's bins between submission and mining changes the payout.
- In volatile markets or during MEV sandwich attacks, this window is routinely exploited.
- The LP has no way to detect or prevent it at the contract level.
- Likelihood is **Medium**: requires concurrent swap activity, which is normal for an active pool.

---

### Recommendation

1. **Core**: Add `uint256 minAmount0Out` and `uint256 minAmount1Out` parameters to `removeLiquidity`; revert if the computed outputs fall below them.
2. **Periphery**: Create a `MetricOmmPoolLiquidityRemover` (mirroring `MetricOmmPoolLiquidityAdder`) that wraps `removeLiquidity` and enforces minimum-output checks after the call returns, analogous to how `addLiquidityExactShares` enforces `maxAmountToken0` / `maxAmountToken1`.

---

### Proof of Concept

1. LP holds shares in bin 0, which currently holds 1 000 token0 and 0 token1.
2. LP submits `removeLiquidity` for 50 % of bin 0 shares, expecting ≈500 token0.
3. Before the LP's tx is mined, a swap sells 1 000 token1 into the pool, buying all 1 000 token0 from bin 0. Bin 0 now holds 0 token0 and ~1 000 token1.
4. LP's tx executes: they receive ≈500 token1 instead of ≈500 token0.
5. If token1 is worth half of token0 at that moment, the LP has lost ~$250 with no revert.
6. No parameter in `removeLiquidity` could have prevented this. [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L1-22)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {Address} from "@openzeppelin/contracts/utils/Address.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {PoolStateLibrary} from "@metric-core/libraries/PoolStateLibrary.sol";
import {PeripheryPayments} from "./base/PeripheryPayments.sol";
import {IMetricOmmPoolLiquidityAdder} from "./interfaces/IMetricOmmPoolLiquidityAdder.sol";
import {IMulticall} from "./interfaces/IMulticall.sol";

/// @title MetricOmmPoolLiquidityAdder
/// @notice Routes `addLiquidity` for EOAs: the pool calls this contract in `metricOmmModifyLiquidityCallback`,
///         which pulls tokens from the user who must have approved this adder beforehand.
/// @dev Layout follows metric-core conventions:
///      constants/state, constructor, external mutators, then internal helpers.
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
contract MetricOmmPoolLiquidityAdder is IMetricOmmPoolLiquidityAdder, PeripheryPayments {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
