### Title
LP Full-Withdrawal DoS via Griefing `addLiquidity` on Victim's Position — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` allows any `msg.sender` to credit shares to an arbitrary `owner` address. `removeLiquidity` reverts with `MinimalLiquidity` when the remaining shares after a burn would be strictly between 0 and `MINIMAL_MINTABLE_LIQUIDITY`. An attacker can front-run a victim's full-withdrawal transaction by adding a dust amount of shares to the victim's position, causing the victim's transaction to revert because the remaining share count (1) falls in the forbidden dust range.

---

### Finding Description

`LiquidityLib.addLiquidity` computes the position key as `(owner, salt, binIdx)` and credits `sharesToAdd` to `positionBinShares[posKey]` without requiring `msg.sender == owner`. [1](#0-0) 

The only guard is the extension-level `DEPOSIT_ALLOWLIST_PROVIDER`, which is absent in the default pool configuration. [2](#0-1) 

`LiquidityLib.removeLiquidity` then enforces:

```solidity
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
``` [3](#0-2) 

This check reverts instead of adjusting the burn amount. The attack path:

1. Victim holds `N` shares in bin `B` with salt `S` and submits `removeLiquidity(owner, S, [{B, N}])` to withdraw everything (leaving 0 shares — valid).
2. Attacker observes the pending transaction, reads `(owner, S, B)` from the calldata, and front-runs with `addLiquidity(owner, S, [{B, 1}])`, paying the proportional token cost for 1 share. Because `userShares + 1 = N + 1 ≥ MINIMAL_MINTABLE_LIQUIDITY`, the add succeeds.
3. Victim's transaction executes: `userShares = N + 1`, `sharesToRemove = N`, `newUserShares = 1`. The check `1 > 0 && 1 < 1000` is true → `revert MinimalLiquidity(1, 1000)`.

The attacker can repeat this every time the victim resubmits, at the cost of 1 share's worth of tokens per attempt (a tiny fraction of the bin balance). [4](#0-3) 

---

### Impact Explanation

An LP attempting a full withdrawal of their position is permanently blocked as long as the attacker is willing to spend dust-level token amounts. The victim's funds are not stolen, but the `removeLiquidity` flow is rendered unusable for the targeted position until the victim discovers the new share count and resubmits — at which point the attacker can front-run again. This matches the "broken core pool functionality / unusable withdraw flow" criterion.

---

### Likelihood Explanation

- No special privilege is required; any unprivileged address can call `addLiquidity` with an arbitrary `owner`.
- The victim's `(owner, salt, binIdx)` tuple is fully visible in the pending transaction's calldata.
- The attacker's per-attempt cost is `1 / binTotalShares` of the bin's token balance — negligible for large bins.
- Pools without a `DEPOSIT_ALLOWLIST_PROVIDER` (the default) are fully exposed. [5](#0-4) 

---

### Recommendation

Two complementary fixes:

1. **In `removeLiquidity`**: when `sharesToRemove == userShares` (full exit), skip the `MinimalLiquidity` dust check entirely, since leaving 0 shares is always valid. The check should only apply to partial removals.

2. **In `addLiquidity`**: optionally restrict adding shares to a position owned by a third party (require `msg.sender == owner` unless an explicit operator approval exists), or document that pools without an allowlist extension are exposed to this griefing vector.

The minimal fix in `LiquidityLib.removeLiquidity`:

```solidity
uint256 newUserShares = userShares - sharesToRemove;
// Only enforce dust floor on partial removals; full exit (newUserShares == 0) is always valid.
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```

This is already the code — the fix is to **cap `sharesToRemove` at `userShares`** before the check, or to allow the caller to specify `type(uint256).max` meaning "remove all", so the pool resolves the exact count at execution time rather than reverting.

---

### Proof of Concept

```
Setup:
  MINIMAL_MINTABLE_LIQUIDITY = 1000
  Victim (Alice) holds 10_000 shares in bin 4, salt = 42.

Step 1 – Alice submits:
  pool.removeLiquidity(alice, 42, [{binIdx: 4, shares: 10_000}], "")
  Expected: removes all shares, receives tokens.

Step 2 – Attacker (Bob) front-runs (same block, higher gas):
  pool.addLiquidity(alice, 42, [{binIdx: 4, shares: 1}], callbackData, "")
  Cost: ~1/10_000 of bin 4's token balance.
  Result: alice's positionBinShares[key(alice,42,4)] = 10_001.

Step 3 – Alice's transaction executes:
  userShares      = 10_001
  sharesToRemove  = 10_000
  newUserShares   = 1
  Check: 1 > 0 && 1 < 1000 → true → revert MinimalLiquidity(1, 1000)

Alice's withdrawal fails. Bob repeats on every resubmission.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L61-79)
```text

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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-111)
```text
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L193-202)
```text
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L93-95)
```text
  /// @notice Deposit allowlist rejected this `owner` for `addLiquidity`.
  /// @dev Only when `DEPOSIT_ALLOWLIST_PROVIDER` is configured on the pool; removal is not subject to the same check.
  error NotAllowedToDeposit();
```

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
