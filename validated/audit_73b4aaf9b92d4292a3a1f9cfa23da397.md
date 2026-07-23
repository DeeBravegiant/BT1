Audit Report

## Title
Permissionless `addLiquidity` Enables Frontrun Griefing to Permanently Lock LP Withdrawals — (File: metric-core/contracts/libraries/LiquidityLib.sol)

## Summary
`addLiquidity` in `MetricOmmPool` accepts an arbitrary `owner` address with no requirement that `msg.sender == owner`, allowing any caller to inject shares into any position. `removeLiquidity` in `LiquidityLib` enforces a `MinimalLiquidity` guard that reverts when the post-removal share count falls in the range `(0, minimalMintableLiquidity)`. An attacker who observes a victim's `removeLiquidity` call can frontrun it by injecting one share into the victim's position, shifting the post-removal remainder from zero into the forbidden range and causing a permanent, repeatable revert. The victim's funds are locked without access to a private mempool.

## Finding Description

**Permissionless deposit to any position.**
`MetricOmmPool.addLiquidity` passes the caller-supplied `owner` directly into `LiquidityLib.addLiquidity` with no `msg.sender == owner` check: [1](#0-0) 

**`MinimalLiquidity` guard in `removeLiquidity`.**
After computing `newUserShares = userShares - sharesToRemove`, `LiquidityLib.removeLiquidity` reverts if the remainder is a non-zero value below `minimalMintableLiquidity`: [2](#0-1) 

**`addLiquidity` minimum check is on the new total, not the delta.**
The guard in `addLiquidity` checks `newUserShares = userShares + sharesToAdd >= minimalMintableLiquidity`. For a position already holding ≥ `minimalMintableLiquidity - 1` shares, adding exactly 1 share passes this check unconditionally: [3](#0-2) 

**Attack sequence:**
1. Alice holds exactly `minimalMintableLiquidity` (e.g., 1000) shares and submits `removeLiquidity(shares=1000)`. Post-removal `newUserShares = 0`, which passes the guard.
2. Bob frontruns with `addLiquidity(owner=Alice, shares=1)`. Alice now holds 1001 shares. Bob's call passes the `addLiquidity` guard because `1001 >= 1000`.
3. Alice's original transaction executes: `newUserShares = 1001 - 1000 = 1`. Since `1 > 0 && 1 < 1000`, the `MinimalLiquidity(1, 1000)` revert fires.
4. Alice re-queries and resubmits `removeLiquidity(shares=1001)`. Bob frontruns again with 1 share → `newUserShares = 1` → revert. Repeatable indefinitely.

**Cost to attacker.**
The token cost for 1 share is computed as `Math.ceilDiv(binState.token0BalanceScaled * 1, binTotalSharesVal)`. For bins where one token balance is zero (e.g., a pure token1 bin), the attacker pays 0 for that token. Even when both balances are non-zero, the cost is at most 1 scaled unit per token — negligible for any bin with a large share count: [4](#0-3) 

**No "remove all" escape hatch.**
`removeLiquidity` requires the caller to specify an exact share count in `deltas.shares`. There is no `type(uint256).max` sentinel or "burn everything" path that bypasses the `MinimalLiquidity` guard when `newUserShares` would be zero: [5](#0-4) 

**`DepositAllowlistExtension` does not mitigate the base pool.**
The allowlist check in `DepositAllowlistExtension.beforeAddLiquidity` is only active when the extension is explicitly configured on a pool. The base pool has no such guard, leaving the injection vector open by default: [6](#0-5) 

## Impact Explanation

An LP's entire principal is permanently locked. Because there is no "remove all" escape hatch and the attacker can always frontrun with one additional share to keep `newUserShares = 1`, the victim cannot execute any withdrawal without a private mempool. This constitutes a direct, indefinitely repeatable loss of user principal and a broken core pool withdraw flow, meeting Sherlock critical/high thresholds.

## Likelihood Explanation

- No special privilege is required; any EOA or contract can call `addLiquidity` for any `owner`.
- The attack is cheap to near-free: at most 1 scaled unit per token per injection, and zero for tokens with no balance in the targeted bin.
- Mempool monitoring is standard for MEV bots.
- The attack is repeatable every time the victim resubmits, making recovery impossible without a private relay.
- The `DepositAllowlistExtension` mitigates this only when explicitly configured; the base pool has no such guard.

## Recommendation

**Option A (preferred):** Treat `sharesToRemove == type(uint256).max` as "burn everything," setting `newUserShares = 0` and bypassing the `MinimalLiquidity` guard in that case. This gives LPs an atomic escape hatch without breaking the operator pattern.

**Option B:** Enforce `msg.sender == owner` inside `addLiquidity`, eliminating the injection vector entirely at the cost of breaking legitimate operator use-cases.

**Option C:** Apply the `MinimalLiquidity` guard to the *delta* being added (not the new total) in `addLiquidity`, so that adding fewer than `minimalMintableLiquidity` shares to any position is rejected. This raises the per-injection cost to at least `minimalMintableLiquidity` shares worth of tokens, making sustained griefing economically unattractive.

## Proof of Concept

```solidity
function testFrontrunRemoveLiquidity() public {
    // Alice adds minimalMintableLiquidity (1000) shares to bin 4.
    vm.prank(alice);
    pool.addLiquidity(alice, SALT, _delta(4, 1000), abi.encode(KIND_PAY), "");

    // Bob frontruns Alice's removeLiquidity(1000) by injecting 1 share.
    vm.prank(bob);
    pool.addLiquidity(alice, SALT, _delta(4, 1), abi.encode(KIND_PAY), "");
    // Alice now has 1001 shares; Bob's call passes addLiquidity guard (1001 >= 1000).

    // Alice's original tx: remove 1000 shares → newUserShares = 1 → revert.
    vm.prank(alice);
    vm.expectRevert(
        abi.encodeWithSelector(IMetricOmmPoolActions.MinimalLiquidity.selector, 1, 1000)
    );
    pool.removeLiquidity(alice, SALT, _delta(4, 1000), "");

    // Bob repeats every time Alice re-queries and resubmits.
    // Alice's funds are permanently locked without a private mempool.
}
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L76-79)
```text
          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L109-111)
```text
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L180-202)
```text
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
