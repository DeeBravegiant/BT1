### Title
Griefer Can Permanently Block LP Full-Withdrawal via Dust Injection into Victim's Position — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` accepts any `owner` address from any `msg.sender`. A griefer can frontrun a victim's full-withdrawal `removeLiquidity` call by injecting a tiny number of shares (as few as 1) into the victim's position. The `removeLiquidity` dust guard then reverts the victim's transaction because the remaining shares fall in the forbidden range `(0, minimalMintableLiquidity)`.

---

### Finding Description

`LiquidityLib.addLiquidity` enforces a minimum-shares floor on the **resulting** position balance:

```solidity
// LiquidityLib.sol L76-79
uint256 newUserShares = userShares + sharesToAdd;
if (newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
``` [1](#0-0) 

Because the check is on `userShares + sharesToAdd`, if the victim already holds `V >= minimalMintableLiquidity` shares, the griefer can pass `sharesToAdd = 1` and the check passes (`V + 1 >= minimalMintableLiquidity`). The griefer pays for 1 share worth of tokens.

`LiquidityLib.removeLiquidity` then enforces the symmetric dust guard on the **remaining** balance:

```solidity
// LiquidityLib.sol L199-202
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
``` [2](#0-1) 

If the victim submitted `sharesToRemove = V` (full withdrawal), after the griefer's injection the pool sees `userShares = V + G` and computes `newUserShares = G`. Since `0 < G < minimalMintableLiquidity`, the guard fires and the victim's transaction reverts.

`MetricOmmPool.addLiquidity` imposes **no ownership check** on the `owner` parameter — any `msg.sender` may credit shares to any address:

```solidity
// MetricOmmPool.sol L182-196
function addLiquidity(
    address owner,   // ← arbitrary, no msg.sender == owner check
    uint80 salt,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
``` [3](#0-2) 

The position key is `keccak256(abi.encode(owner, salt, binIdx))`, all of which are visible in the victim's pending mempool transaction. [4](#0-3) 

---

### Impact Explanation

An LP's `removeLiquidity` call for a full-position exit can be denied indefinitely. The griefer re-injects 1 share each time the victim retries. The victim's principal is locked in the pool: they cannot withdraw their tokens. This is broken core pool functionality causing loss of access to LP assets, satisfying the "unusable withdraw flow" impact gate.

---

### Likelihood Explanation

- Requires mempool visibility (standard on all public EVM chains) and a single frontrun per victim attempt.
- No special role or permission is needed — any unprivileged address can call `addLiquidity` for any `owner`.
- Cost to griefer per block: the token value of 1 share (dust), which is negligible relative to the victim's locked principal.
- The attack is repeatable indefinitely at near-zero cost.

Likelihood: **Medium** (requires active frontrunning, but is trivially cheap and repeatable).

---

### Recommendation

Restrict `addLiquidity` so that only `msg.sender == owner` (or an approved operator) can credit shares to a position, mirroring the `msg.sender != owner` guard already present on `removeLiquidity`:

```solidity
// MetricOmmPool.sol — addLiquidity
if (msg.sender != owner) revert NotPositionOwner();
```

Alternatively, if the operator pattern (paying on behalf of another owner) must be preserved, introduce an explicit allowance or approval mapping so that only the owner or a pre-approved operator can add shares to a given `(owner, salt)` position.

---

### Proof of Concept

```
Setup:
  MINIMAL_MINTABLE_LIQUIDITY = 1000 (pool default in tests)
  Victim owns 10 000 shares in bin 4, salt = 0

Step 1 — Victim broadcasts:
  removeLiquidity(owner=victim, salt=0, [{bin:4, shares:10000}])
  → intended result: full exit, newUserShares = 0 → allowed

Step 2 — Griefer sees tx in mempool, frontruns with higher gas:
  addLiquidity(owner=victim, salt=0, [{bin:4, shares:1}])
  Check: newUserShares = 10000 + 1 = 10001 >= 1000 → passes
  Griefer pays token cost of 1 share (dust)

Step 3 — Victim's tx executes:
  userShares = 10001, sharesToRemove = 10000
  newUserShares = 10001 - 10000 = 1
  1 > 0 && 1 < 1000 → revert MinimalLiquidity(1, 1000)

Step 4 — Victim retries; griefer repeats Step 2.
  Victim's principal remains locked indefinitely.
```

The griefer's `addLiquidity` call passes the `MinimalLiquidity` guard because the victim's pre-existing shares already satisfy the floor; only 1 additional share is needed. The victim cannot escape by specifying `sharesToRemove = 10001` because they do not know the griefer will inject, and even if they do, the griefer simply injects 2 shares next time.

### Citations

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L256-259)
```text
  function _positionBinKey(address owner, uint80 salt, int8 bin) internal pure returns (bytes32 key) {
    // forge-lint: disable-next-line(asm-keccak256)
    return keccak256(abi.encode(owner, salt, bin));
  }
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
