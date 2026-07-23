### Title
Griefing DoS on `removeLiquidity` via Permissionless `addLiquidity` Owner Injection — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` accepts a caller-supplied `owner` address with no restriction that `msg.sender == owner`. Any attacker can therefore credit shares into a victim's position at negligible cost. Because `removeLiquidity` reverts with `MinimalLiquidity` whenever the post-burn share count is non-zero but below `MINIMAL_MINTABLE_LIQUIDITY`, an attacker can permanently block a victim's full-exit by frontrunning every withdrawal with a 1-share donation, leaving a dust remainder that trips the guard.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes the caller-supplied `owner` directly into `LiquidityLib.addLiquidity` without verifying `msg.sender == owner`:

```solidity
// MetricOmmPool.sol L182-196
function addLiquidity(
    address owner,          // ← any address, no ownership check
    uint80 salt,
    LiquidityDelta calldata deltas,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

Inside `LiquidityLib.addLiquidity`, the only guard on the resulting share count is:

```solidity
// LiquidityLib.sol L76-79
uint256 newUserShares = userShares + sharesToAdd;
if (newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(...);
}
```

If the victim already holds `N >= MINIMAL_MINTABLE_LIQUIDITY` shares, adding even 1 share satisfies this check (`N + 1 >= MINIMAL_MINTABLE_LIQUIDITY`).

`removeLiquidity` enforces the symmetric guard on the post-burn remainder:

```solidity
// LiquidityLib.sol L199-202
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```

**Attack sequence:**

1. Victim holds `N` shares in bin B and submits `removeLiquidity(sharesToRemove = N)` to fully exit.
2. Attacker observes the pending transaction and frontruns with `addLiquidity(owner = victim, sharesToAdd = 1)`.
3. Victim's share count becomes `N + 1`.
4. Victim's `removeLiquidity(N)` executes: `newUserShares = 1`, which satisfies `1 > 0 && 1 < MINIMAL_MINTABLE_LIQUIDITY` → **reverts**.
5. Attacker repeats on every retry.

The cost to the attacker per frontrun is `ceil(binState.token0BalanceScaled / binTotalSharesVal)` scaled units for 1 share. After descaling via `_deltasScaledToExternal`, this rounds up to at most 1 native token unit — effectively 1 wei for an 18-decimal token. The attacker's expenditure is negligible and the tokens are donated to the victim's position (not recoverable by the attacker), making the attack economically viable indefinitely.

---

### Impact Explanation

A victim LP cannot fully exit any bin position as long as the attacker keeps frontrunning. The victim's principal (token0 and token1 locked in the bin) is inaccessible. Partial withdrawals that leave `>= MINIMAL_MINTABLE_LIQUIDITY` shares succeed, but the victim can never reach zero and reclaim the final tranche. This constitutes a direct, persistent loss of access to user principal — broken core pool withdraw functionality.

---

### Likelihood Explanation

- `addLiquidity` is a public, permissionless function with no `msg.sender == owner` guard.
- The attacker needs only a standard EOA and a trivial token balance (1 wei per frontrun).
- No privileged role, special setup, or non-standard token is required.
- The attack is repeatable at negligible cost and cannot be mitigated by the victim alone without a protocol-level fix.

---

### Recommendation

**Option A (preferred):** In `removeLiquidity`, cap `sharesToRemove` at `userShares` when the caller requests more than they own, or add a "remove all" path that sets `sharesToRemove = userShares` unconditionally. This lets a victim always fully exit regardless of injected dust:

```solidity
// LiquidityLib.sol removeLiquidity — inside the per-bin loop
uint256 sharesToRemove = deltas.shares[i];
if (sharesToRemove > userShares) sharesToRemove = userShares; // cap to full exit
uint256 newUserShares = userShares - sharesToRemove;
```

**Option B:** Restrict `addLiquidity` so that `msg.sender == owner` (or require explicit approval), preventing third-party share injection entirely.

---

### Proof of Concept

```
State before attack:
  victim.shares[bin=4] = 10_000   (MINIMAL_MINTABLE_LIQUIDITY = 1_000)

Step 1 — victim submits:
  removeLiquidity(owner=victim, salt=S, shares=[10_000])

Step 2 — attacker frontruns (costs ~1 wei of token0):
  addLiquidity(owner=victim, salt=S, binIdxs=[4], shares=[1])
  → victim.shares[bin=4] = 10_001

Step 3 — victim's tx executes:
  newUserShares = 10_001 - 10_000 = 1
  1 > 0 && 1 < 1_000  →  revert MinimalLiquidity(1, 1_000)

Step 4 — victim retries with shares=[10_001]:
  attacker frontruns again with shares=[1]
  → victim.shares = 10_002, victim's tx reverts again.

Victim's principal is permanently locked.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L76-79)
```text
          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L196-202)
```text
          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```
