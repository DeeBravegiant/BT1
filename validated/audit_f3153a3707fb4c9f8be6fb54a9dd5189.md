### Title
Unauthenticated `addLiquidity` Enables Front-Running DoS on LP Full Withdrawal via `MinimalLiquidity` Revert — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` in `MetricOmmPool` accepts an arbitrary `owner` address with no requirement that `msg.sender == owner`. Any caller can therefore deposit tokens into another user's position. Combined with the `MinimalLiquidity` dust guard in `removeLiquidity`, a malicious actor can front-run a victim LP's full-withdrawal transaction with a tiny deposit into the victim's position, causing the victim's withdrawal to revert because the residual share count falls in the forbidden range `(0, minimalMintableLiquidity)`.

---

### Finding Description

`removeLiquidity` enforces `msg.sender == owner` before burning shares: [1](#0-0) 

`addLiquidity` has no equivalent guard — any caller may credit shares to any `owner`: [2](#0-1) 

Inside `LiquidityLib.addLiquidity`, shares are written to the position key `(owner, salt, bin)` derived from the caller-supplied `owner`, not from `msg.sender`: [3](#0-2) 

Inside `LiquidityLib.removeLiquidity`, after subtracting the requested shares, the dust guard fires whenever the remainder is non-zero but below `minimalMintableLiquidity`: [4](#0-3) 

Because the attacker controls how many shares they inject, they can choose `sharesToAdd` such that `(victimCurrentShares + sharesToAdd) - victimIntendedRemoval` lands in `(0, minimalMintableLiquidity)`, triggering the revert.

---

### Impact Explanation

The victim LP's `removeLiquidity` transaction reverts. Their tokens remain locked in the pool. The attacker can repeat the front-run on every retry, creating a persistent DoS on the victim's withdrawal path. This breaks the core liquidity-removal flow for targeted positions.

The attacker's deposited tokens are permanently credited to the victim's position key; only the victim can later withdraw them. This makes the attack economically costly to sustain, but does not prevent it — a motivated attacker (e.g., one who profits from keeping the LP locked in a related protocol) can execute it repeatedly at low per-block cost (as few as 1 share worth of tokens per front-run).

---

### Likelihood Explanation

- The victim's `(owner, salt, bin)` tuple is fully observable from their pending mempool transaction or prior on-chain history.
- No privileged role is required; any EOA or contract can call `addLiquidity` with a victim's `owner` and `salt`.
- The attack is most effective against LPs attempting a full exit (all shares in a bin), which is a common operation.
- The attacker must pay proportional tokens per front-run, limiting casual abuse, but does not prevent targeted griefing.

---

### Recommendation

Add an `msg.sender == owner` authorization check to `addLiquidity`, mirroring the guard already present in `removeLiquidity`:

```solidity
// MetricOmmPool.sol – addLiquidity
if (msg.sender != owner) revert NotPositionOwner();
```

If third-party deposits on behalf of an owner are an intentional design feature, introduce an explicit approval mapping (e.g., `approvedDepositors[owner][depositor]`) so that only addresses whitelisted by the owner can inject shares into their position.

---

### Proof of Concept

```
State: victim holds 10 000 shares in bin 4, salt S.
       minimalMintableLiquidity = 1 000.

Step 1 – Victim submits:
  pool.removeLiquidity(victim, S, [{bin:4, shares:10_000}], "")

Step 2 – Attacker front-runs (same block, higher gas):
  pool.addLiquidity(victim, S, [{bin:4, shares:500}], callbackData, "")
  // Attacker pays tokens; victim's positionBinShares[(victim,S,4)] becomes 10 500.

Step 3 – Victim's tx executes:
  userShares      = 10 500
  sharesToRemove  = 10 000
  newUserShares   = 500          // > 0 and < 1 000
  → revert MinimalLiquidity(500, 1000)

Step 4 – Victim's funds remain locked.
         Attacker repeats Step 2 on every retry.
```

The 500 injected shares are permanently locked in the victim's position key; the victim can eventually recover them by querying their live share balance and removing the full updated amount — but the attacker can keep front-running each attempt, sustaining the DoS indefinitely at the cost of 500 shares of tokens per block.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L72-79)
```text
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

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
