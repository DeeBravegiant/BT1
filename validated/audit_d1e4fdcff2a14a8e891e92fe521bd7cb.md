All three code claims check out against the actual source:

1. `addLiquidity` — no ownership check on `owner` parameter. [1](#0-0) 

2. `removeLiquidity` — enforces `msg.sender == owner`. [2](#0-1) 

3. `MinimalLiquidity` guard fires when `newUserShares > 0 && newUserShares < minimalMintableLiquidity`. [3](#0-2) 

4. The callback at line 147 means the attacker must be a contract implementing `IMetricOmmModifyLiquidityCallback`, which is a trivial precondition. [4](#0-3) 

The position key is `keccak256(owner, salt, bin)`, so Bob's donation is permanently credited to Alice's key and irrecoverable by Bob, but the griefing effect on Alice's withdrawal is real and repeatable. [5](#0-4) 

---

Audit Report

## Title
Unrestricted `addLiquidity` Enables Front-Run Griefing That Permanently Blocks LP Full-Withdrawal — (`metric-core/contracts/MetricOmmPool.sol` / `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`addLiquidity` accepts an arbitrary `owner` address with no caller restriction, while `removeLiquidity` enforces `msg.sender == owner`. An attacker can front-run a victim's "remove all shares" transaction by donating a tiny number of shares to the victim's position, causing the victim's transaction to revert because the residual share balance falls below `MINIMAL_MINTABLE_LIQUIDITY`. The attack is repeatable at negligible cost per round and permanently blocks the victim's normal withdrawal path.

## Finding Description
`addLiquidity` in `MetricOmmPool.sol` (L182–196) imposes no `msg.sender == owner` check, allowing any caller to inject shares into any `(owner, salt, bin)` position key. `removeLiquidity` (L199–212) correctly restricts callers with `if (msg.sender != owner) revert NotPositionOwner()` (L206).

Inside `LiquidityLib.removeLiquidity`, after computing `newUserShares = userShares - sharesToRemove` (L199), the guard at L200–202 reverts if `newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity`. This guard exists to prevent dust positions but has no exception for full-exit attempts.

Attack path:
1. Alice holds `N` shares (e.g. `10 000`) in bin `b` under `(alice, salt)`.
2. Alice broadcasts `removeLiquidity(alice, salt, {bin: b, shares: 10000}, "")`.
3. Bob (a contract implementing `IMetricOmmModifyLiquidityCallback`) front-runs with `addLiquidity(alice, salt, {bin: b, shares: 1}, ...)`, paying the token cost of 1 share.
4. Alice's position becomes `10 001` shares.
5. Alice's transaction executes: `newUserShares = 10001 - 10000 = 1`. Since `1 > 0 && 1 < 1000`, the pool reverts with `MinimalLiquidity(1, 1000)`.
6. Bob's donated tokens are permanently credited to Alice's position key (`keccak256(alice, salt, bin)`) and cannot be recovered by Bob because `removeLiquidity` requires `msg.sender == alice`. The attack cost is the token value of 1 share per round.

Existing guards are insufficient: the add-side minimum check (L76–79) passes because Alice's existing shares already exceed `minimalMintableLiquidity`, so adding 1 share keeps `newUserShares` above the threshold.

## Impact Explanation
An LP attempting a full exit from a bin position is permanently blocked from doing so via the normal `removeLiquidity` path as long as the attacker is willing to spend the token value of 1 share per attempt. The LP's funds are not stolen but are rendered inaccessible through the standard withdrawal flow. This constitutes broken core pool functionality causing an unusable withdraw flow, meeting the contest's "broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" impact gate.

## Likelihood Explanation
The victim's `(owner, salt, bin, shares)` tuple is fully visible in the mempool. The attacker requires only a trivial token balance (1 share worth) and a contract implementing the callback interface. No privileged role is required. The attack is repeatable at negligible cost per round. Front-running capability is standard on most EVM chains. Likelihood: Medium.

## Recommendation
**Option A (preferred):** Add `if (msg.sender != owner) revert NotPositionOwner();` to `addLiquidity`, consistent with `removeLiquidity`. Third-party deposits can be handled via an explicit allowance or extension mechanism.

**Option B:** In `removeLiquidity`, skip the `MinimalLiquidity` check when `sharesToRemove == userShares` (full exit to zero is always safe):
```solidity
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```
This requires callers to read the current on-chain share count immediately before submitting, or support a "remove all" sentinel value.

**Option C:** Accept a `maxSharesHeld` slippage parameter in `removeLiquidity` so the transaction reverts if the attacker has inflated the position beyond what the user expected.

## Proof of Concept
```solidity
// Alice has 10_000 shares in bin 4, salt = 12345
// MINIMAL_MINTABLE_LIQUIDITY = 1000

// Step 1: Alice broadcasts
pool.removeLiquidity(alice, 12345, LiquidityDelta({
    binIdxs: toArray(int256(4)),
    shares:  toArray(uint256(10_000))
}), "");

// Step 2: Bob (attacker contract) front-runs
// Bob implements IMetricOmmModifyLiquidityCallback and pays 1 share of token0
pool.addLiquidity(alice, 12345, LiquidityDelta({
    binIdxs: toArray(int256(4)),
    shares:  toArray(uint256(1))
}), callbackData, "");
// positionBinShares[keccak256(alice,12345,4)] is now 10_001

// Step 3: Alice's transaction executes:
//   newUserShares = 10_001 - 10_000 = 1
//   1 > 0 && 1 < 1000 → revert MinimalLiquidity(1, 1000)
// Alice's withdrawal is blocked. Bob repeats for each retry.
```

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
