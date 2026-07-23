Audit Report

## Title
Unrestricted `addLiquidity` Owner Parameter Enables Griefable DoS on Any LP's Full `removeLiquidity` — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no `msg.sender == owner` restriction, allowing any caller (via a contract implementing the callback) to credit shares into any LP's position. `LiquidityLib.removeLiquidity` reverts when the post-removal share count falls in `(0, minimalMintableLiquidity)`. An attacker can frontrun any full-withdrawal transaction by donating 1 share to the victim's position, causing the victim's `removeLiquidity` to leave exactly 1 dust share and revert. The attack is repeatable at negligible cost, permanently blocking the victim's withdrawal in a public mempool.

## Finding Description

`MetricOmmPool.addLiquidity` (lines 182–196) accepts an explicit `owner` parameter and performs no check that `msg.sender == owner`. The only pre-call hook is `_beforeAddLiquidity`, which dispatches to optional pool extensions — no built-in access control exists:

```solidity
// MetricOmmPool.sol L182-196
function addLiquidity(
    address owner,   // ← arbitrary; caller pays, position credited to owner
    uint80 salt,
    LiquidityDelta calldata deltas,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

`LiquidityLib.addLiquidity` (lines 76–79) only guards that the post-add share count meets the minimum — a donation to a victim who already holds `N ≥ minimalMintableLiquidity` trivially passes (`N+1 ≥ minimalMintableLiquidity`):

```solidity
// LiquidityLib.sol L76-79
uint256 newUserShares = userShares + sharesToAdd;
if (newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(...);
}
```

`LiquidityLib.removeLiquidity` (lines 199–202) then reverts if the remaining shares fall in the dust range:

```solidity
// LiquidityLib.sol L199-202
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```

`removeLiquidity` enforces `msg.sender == owner` (line 206), so only the victim can call it — but the victim's cached `sharesToRemove = N` now leaves `newUserShares = 1`, satisfying `0 < 1 < minimalMintableLiquidity` and reverting. The attacker need only implement `IMetricOmmModifyLiquidityCallback` (a trivial contract) to satisfy the callback requirement at `LiquidityLib.sol` line 147.

## Impact Explanation

The withdrawal flow — a core pool function — is rendered unusable for any LP on a public mempool chain. Smart-contract vaults or routers that call `removeLiquidity` with a cached share count and do not handle `MinimalLiquidity` reverts can have their withdrawal logic permanently bricked. This maps directly to the allowed impact: **broken core pool functionality causing unusable withdraw flows**.

## Likelihood Explanation

The attack requires only a contract implementing `IMetricOmmModifyLiquidityCallback` and a standard `addLiquidity` call with `owner = victim`. No privileged role is needed. The attacker's token cost is bounded by 1 share (potentially 1 wei for high-liquidity bins) plus gas. The attack is repeatable on every retry, forcing the victim to use a private mempool or MEV-protected RPC. The donated tokens are credited to the victim's position, so the attacker bears a small permanent token loss, but it can be negligible.

## Recommendation

**Option A (preferred):** Restrict `addLiquidity` so that `msg.sender == owner` unless an explicit delegation/approval mechanism is in place. This eliminates the donation vector entirely.

**Option B:** In `LiquidityLib.removeLiquidity`, allow a full-to-zero withdrawal even when the remaining amount would be dust — only block partial withdrawals that leave dust. Change the guard to:
```solidity
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity && newUserShares != userShares) {
    revert ...;
}
```
(i.e., skip the guard when `sharesToRemove == userShares`).

**Option C:** Add a caller-supplied `minSharesAfter` slippage parameter so the LP controls the invariant rather than the pool enforcing a fixed floor exploitable by third parties.

## Proof of Concept

1. LP calls `addLiquidity(owner=LP, salt=S, shares=[10_000])` → position `(LP, S, bin)` holds `N = 10_000` shares (`minimalMintableLiquidity = 1_000`).
2. LP broadcasts `removeLiquidity(owner=LP, salt=S, shares=[10_000])`.
3. Attacker (a contract implementing `IMetricOmmModifyLiquidityCallback`) sees the pending tx and frontruns with `addLiquidity(owner=LP, salt=S, shares=[1])`. Add-side guard: `10_001 ≥ 1_000` → passes.
4. LP's `removeLiquidity` executes: `userShares = 10_001`, `sharesToRemove = 10_000`, `newUserShares = 1`.
5. Guard: `1 > 0 && 1 < 1_000` → `revert MinimalLiquidity(1, 1_000)`.
6. LP's withdrawal is blocked. Attacker repeats on every retry.

Foundry test plan: deploy a mock callback contract, `addLiquidity` for LP, then call `addLiquidity(owner=LP, shares=[1])` from the attacker contract, then assert that LP's `removeLiquidity(shares=[N])` reverts with `MinimalLiquidity`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L76-79)
```text
          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L199-202)
```text
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```
