Audit Report

## Title
Griefer Can Permanently Block LP Full-Withdrawal via Dust Injection into Victim's Position — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from any `msg.sender` with no ownership check, allowing any caller to credit shares to any existing position. A griefer can frontrun a victim's full-withdrawal `removeLiquidity` call by injecting a dust amount of shares (as few as 1) into the victim's position. The `removeLiquidity` dust guard then reverts the victim's transaction because the remaining shares fall in the forbidden range `(0, minimalMintableLiquidity)`, locking the victim's principal indefinitely.

## Finding Description
`MetricOmmPool.addLiquidity` (L182–196) passes the caller-supplied `owner` directly into `LiquidityLib.addLiquidity` with no `msg.sender == owner` check:

```solidity
// MetricOmmPool.sol L182-196
function addLiquidity(
    address owner,   // ← arbitrary, no msg.sender == owner check
    uint80 salt,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

By contrast, `removeLiquidity` (L206) enforces `if (msg.sender != owner) revert NotPositionOwner()`.

`LiquidityLib.addLiquidity` (L76–79) checks only that the **resulting** position balance meets the minimum floor:

```solidity
uint256 newUserShares = userShares + sharesToAdd;
if (newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(...);
}
```

If the victim already holds `V >= minimalMintableLiquidity` shares, the griefer passes `sharesToAdd = G` (e.g., 1) and the check passes (`V + G >= minimalMintableLiquidity`).

`LiquidityLib.removeLiquidity` (L199–202) then enforces the symmetric dust guard on the **remaining** balance:

```solidity
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(...);
}
```

When the victim submits `sharesToRemove = V` (full exit), after the griefer's injection the pool sees `userShares = V + G` and computes `newUserShares = G`. Since `0 < G < minimalMintableLiquidity`, the guard fires and the victim's transaction reverts.

The position key `keccak256(abi.encode(owner, salt, bin))` (L256–259) is fully deterministic from data visible in the victim's pending mempool transaction. The griefer must implement `IMetricOmmModifyLiquidityCallback` (called at L147), which is trivially satisfied by deploying a minimal contract that transfers the dust token amount in the callback.

## Impact Explanation
The victim's LP principal is locked in the pool: they cannot execute a full-position exit. The griefer re-injects dust shares each time the victim retries, repeating the block indefinitely. This constitutes broken core pool functionality causing loss of access to LP assets — matching the "unusable withdraw flow" impact gate. Severity: **Medium** (griefing/DoS with indefinite fund lockup, no direct theft).

## Likelihood Explanation
- Requires only mempool visibility (standard on all public EVM chains) and a single frontrun per victim attempt.
- No special role or permission is needed — any unprivileged address can call `addLiquidity` for any `owner`.
- Cost to griefer per block: token value of `G` shares (dust), negligible relative to the victim's locked principal.
- The attack is repeatable indefinitely at near-zero cost.
- Likelihood: **Medium** (requires active frontrunning, but is trivially cheap and repeatable).

## Recommendation
Restrict `addLiquidity` so that only `msg.sender == owner` (or an approved operator) can credit shares to a position, mirroring the guard already present on `removeLiquidity`:

```solidity
// MetricOmmPool.sol — addLiquidity
if (msg.sender != owner) revert NotPositionOwner();
```

Alternatively, if paying on behalf of another owner must be supported, introduce an explicit allowance or approval mapping so that only the owner or a pre-approved operator can add shares to a given `(owner, salt)` position.

## Proof of Concept
```
Setup:
  MINIMAL_MINTABLE_LIQUIDITY = 1000
  Victim owns 10,000 shares in bin 4, salt = 0

Step 1 — Victim broadcasts:
  removeLiquidity(owner=victim, salt=0, [{bin:4, shares:10000}])
  → intended: newUserShares = 0 → allowed

Step 2 — Griefer deploys callback contract, frontruns with higher gas:
  addLiquidity(owner=victim, salt=0, [{bin:4, shares:1}])
  Check: newUserShares = 10000 + 1 = 10001 >= 1000 → passes
  Griefer pays token cost of 1 share (dust)

Step 3 — Victim's tx executes:
  userShares = 10001, sharesToRemove = 10000
  newUserShares = 10001 - 10000 = 1
  1 > 0 && 1 < 1000 → revert MinimalLiquidity(1, 1000)

Step 4 — Victim retries; griefer repeats Step 2.
  Victim's principal remains locked indefinitely.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
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
