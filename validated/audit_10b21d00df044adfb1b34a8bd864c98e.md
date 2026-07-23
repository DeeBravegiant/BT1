Audit Report

## Title
Attacker Can Permanently DoS Any LP's `removeLiquidity` by Frontrunning with a Dust Share Donation — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no `msg.sender == owner` restriction, allowing any caller to credit shares into any LP's position. `LiquidityLib.removeLiquidity` enforces a `MinimalLiquidity` guard that reverts when the remaining position shares fall in the range `(0, minimalMintableLiquidity)`. An attacker can frontrun any full-withdrawal transaction by donating exactly 1 share to the victim's position, causing the victim's `sharesToRemove` to leave exactly 1 dust share, which triggers the revert. The attacker can repeat this on every retry, indefinitely blocking the victim's withdrawal.

## Finding Description

`MetricOmmPool.addLiquidity` (lines 182–196) accepts an explicit `owner` parameter and imposes no `msg.sender == owner` check:

```solidity
function addLiquidity(
    address owner,   // ← any address; caller pays, position credited to owner
    uint80 salt,
    LiquidityDelta calldata deltas,
    ...
```

`_beforeAddLiquidity` only invokes optional pool extensions; there is no built-in access control preventing a third party from crediting shares to an arbitrary position.

`removeLiquidity` (line 206) enforces `msg.sender == owner`, so only the position owner can withdraw. It delegates to `LiquidityLib.removeLiquidity`, which contains:

```solidity
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```

The `addLiquidity` path's own guard (lines 76–79) only checks whether the post-add total is below the minimum:

```solidity
uint256 newUserShares = userShares + sharesToAdd;
if (newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(...);
}
```

Because the victim already holds `N ≥ minimalMintableLiquidity` shares, donating 1 share yields `N+1 ≥ minimalMintableLiquidity`, passing the add-side guard. The victim's pending `removeLiquidity(shares=N)` then computes `newUserShares = 1`, satisfying `0 < 1 < minimalMintableLiquidity`, and reverts.

The victim cannot escape by querying their updated balance and retrying with `sharesToRemove = N+1` (all shares), because the attacker can frontrun that transaction too, donating 1 more share to make `newUserShares = 1` again.

## Impact Explanation

This directly matches the allowed impact gate: **broken core pool functionality causing unusable withdraw flows**. Any LP's full-withdrawal can be griefed indefinitely at minimal cost. Smart-contract vaults or routers that call `removeLiquidity` with a cached share count and do not handle `MinimalLiquidity` reverts gracefully will have their withdrawal flow permanently bricked. While the victim's principal is not permanently destroyed (it remains in the pool), the withdrawal function is rendered non-functional for as long as the attacker continues the grief.

## Likelihood Explanation

The attack requires only a standard `addLiquidity` call with `owner = victim`; no special role or privileged access is needed. The attacker's cost per grief is bounded by 1 share worth of tokens (potentially 1 wei for high-liquidity bins) plus gas. The donated tokens are credited to the victim's position, so the attacker bears a permanent token loss — but it can be negligible. Frontrunning is straightforward on any public mempool chain, and the attack is trivially repeatable.

## Recommendation

**Option A (preferred):** Restrict `addLiquidity` so that `msg.sender == owner` unless an explicit delegation mechanism (e.g., an approved-operator mapping) is in place. This eliminates the donation vector entirely.

**Option B:** In `LiquidityLib.removeLiquidity`, allow a full-to-zero withdrawal even when the remaining amount would be dust — only block partial withdrawals that leave dust. Change the guard to:
```solidity
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity && newUserShares != 0) {
```
More precisely: skip the `MinimalLiquidity` revert when `sharesToRemove == userShares` (i.e., the caller is withdrawing their entire position).

**Option C:** Add a slippage-style parameter (`minSharesAfter`) that the caller sets, reverting only when actual remaining shares fall below that caller-supplied floor, giving the LP control over the invariant rather than the pool enforcing a fixed floor exploitable by third parties.

## Proof of Concept

1. LP calls `addLiquidity(owner=LP, salt=S, shares=[10_000])` → position `(LP, S, bin)` holds `N = 10_000` shares (`minimalMintableLiquidity = 1_000`).
2. LP broadcasts `removeLiquidity(owner=LP, salt=S, shares=[10_000])` to withdraw everything.
3. Attacker sees the pending tx in the mempool and frontruns with `addLiquidity(owner=LP, salt=S, shares=[1])`. The add-side guard passes: `10_000 + 1 = 10_001 ≥ 1_000`.
4. LP's `removeLiquidity` executes: `userShares = 10_001`, `sharesToRemove = 10_000`, `newUserShares = 1`.
5. Guard fires at `LiquidityLib.sol` line 200: `1 > 0 && 1 < 1_000` → `revert MinimalLiquidity(1, 1_000)`.
6. LP retries with `sharesToRemove = 10_001`; attacker frontruns again with `shares=[1]`, making `userShares = 10_002`, `newUserShares = 1` → same revert.
7. Repeat indefinitely.

Foundry test plan: deploy pool with `minimalMintableLiquidity = 1_000`, add `10_000` shares as LP, then from a second address call `addLiquidity(owner=LP, shares=[1])` and assert that LP's subsequent `removeLiquidity(shares=10_000)` reverts with `MinimalLiquidity(1, 1_000)`.