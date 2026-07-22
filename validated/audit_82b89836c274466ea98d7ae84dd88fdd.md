### Title
Attacker Can Frontrun `removeLiquidity` to Permanently Grief LP Withdrawals via Dust Share Injection — (File: metric-core/contracts/libraries/LiquidityLib.sol)

---

### Summary

`addLiquidity` permits any `msg.sender` to credit shares to an arbitrary `owner` address (the documented operator pattern). `removeLiquidity` enforces a `MinimalLiquidity` guard that reverts when the position's remaining shares fall in the range `(0, minimalMintableLiquidity)`. An attacker who observes a victim's `removeLiquidity` call in the mempool can frontrun it by injecting a single dust share into the victim's position, shifting the post-removal remainder from zero into the forbidden range and causing the victim's transaction to revert. Because the attacker can repeat this at negligible cost, the victim can be permanently prevented from executing any partial withdrawal.

---

### Finding Description

**Permissionless deposit to any position.**
`addLiquidity` accepts an `owner` argument that need not equal `msg.sender`: [1](#0-0) 

The interface documents this explicitly: *"msg.sender pays but need not equal owner (operator pattern)."* [2](#0-1) 

**`MinimalLiquidity` guard in `removeLiquidity`.**
After computing the post-removal share count, `LiquidityLib.removeLiquidity` reverts if the remainder is a non-zero dust value: [3](#0-2) 

**`addLiquidity` minimum check is on the new total, not the delta.**
The test `test_modifyLiquidity_revertsWhenMintBelowMinimalLiquidity` shows that adding 999 shares to a fresh (zero-share) position emits `MinimalLiquidity(999, 1000)`, consistent with a check on `newUserShares = existing + delta`. For a position that already holds ≥ 999 shares, adding 1 share produces `newUserShares ≥ 1000`, which passes the guard. Bob can therefore inject exactly 1 share into Alice's existing position. [4](#0-3) 

**Attack sequence (repeatable):**

| Step | Alice's shares | Bob's action | Alice's tx result |
|---|---|---|---|
| 1 | 1 000 | — | `removeLiquidity(1000)` → `newUserShares=0` → **passes** |
| 2 | 1 000 | `addLiquidity(owner=Alice, shares=1)` → Alice now has 1 001 | `removeLiquidity(1000)` → `newUserShares=1` → **MinimalLiquidity revert** |
| 3 | 1 001 | Alice re-queries, submits `removeLiquidity(1001)` | Bob adds 1 again → `newUserShares=1` → **MinimalLiquidity revert** |
| … | … | Bob repeats at negligible cost | Alice is permanently blocked |

**Cost to attacker.** The token amount owed for 1 share is `binState.token(0|1)BalanceScaled × 1 / binTotalShares`, which rounds to zero whenever `binTotalShares > binState.token(0|1)BalanceScaled`. In practice, for any bin with a large share count relative to its scaled balance, the attacker pays 0 tokens per injection. [5](#0-4) 

---

### Impact Explanation

An LP's liquidity is permanently locked. Because `removeLiquidity` requires the caller to specify an exact share count and there is no "remove all" escape hatch, the attacker can always frontrun with one additional share, keeping the post-removal remainder at 1 and triggering `MinimalLiquidity` indefinitely. The victim cannot recover principal without access to a private mempool. This constitutes a direct loss of user principal / unusable withdraw flow above Sherlock thresholds.

---

### Likelihood Explanation

- No special privilege is required; any EOA or contract can call `addLiquidity` for any `owner`.
- The attack is cheap to near-free (0 tokens when scaled balance rounds down).
- Mempool monitoring is standard for MEV bots.
- The `DepositAllowlistExtension` mitigates this only when explicitly configured; the base pool has no such guard. [6](#0-5) 

---

### Recommendation

**Option A (preferred):** Add a "remove all" variant or allow `sharesToRemove == type(uint256).max` to mean "burn everything," bypassing the `MinimalLiquidity` check when `newUserShares` would be zero. This preserves the operator pattern while giving LPs an atomic escape.

**Option B:** Enforce `msg.sender == owner` inside `addLiquidity` (remove the operator pattern). This eliminates the injection vector entirely but breaks legitimate operator use-cases.

**Option C:** Apply the `MinimalLiquidity` guard only to the *delta* being added (not the new total), so that adding fewer than `minimalMintableLiquidity` shares to an existing position is rejected. This raises the cost of each injection to at least `minimalMintableLiquidity` shares worth of tokens, making sustained griefing economically unattractive.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume standard test harness with pool, token0, token1, alice, bob.

function testFrontrunRemoveLiquidity() public {
    // Alice adds 1000 shares (= minimalMintableLiquidity) to bin 4.
    vm.prank(alice);
    pool.addLiquidity(alice, SALT, _delta(4, 1000), abi.encode(KIND_PAY), "");

    // Alice's position: 1000 shares. She intends to remove all 1000.
    // newUserShares = 0 → would pass MinimalLiquidity guard.

    // Bob observes Alice's pending removeLiquidity(1000) in the mempool.
    // Bob frontruns: adds 1 share to Alice's position (costs 0 tokens if
    // token0BalanceScaled / 1000 rounds to 0).
    vm.prank(bob);
    pool.addLiquidity(alice, SALT, _delta(4, 1), abi.encode(KIND_PAY), "");
    // Alice now has 1001 shares.

    // Alice's original transaction executes: remove 1000 shares.
    // newUserShares = 1001 - 1000 = 1.
    // 1 > 0 && 1 < 1000 → MinimalLiquidity(1, 1000) revert.
    vm.prank(alice);
    vm.expectRevert(
        abi.encodeWithSelector(IMetricOmmPoolActions.MinimalLiquidity.selector, 1, 1000)
    );
    pool.removeLiquidity(alice, SALT, _delta(4, 1000), "");

    // Bob repeats every time Alice re-queries and resubmits.
    // Alice's funds are permanently locked without a private mempool.
}
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L196-214)
```text
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
```

**File:** metric-core/test/MetricOmmPool.modifyLiquidity.t.sol (L657-667)
```text
  function test_modifyLiquidity_revertsWhenMintBelowMinimalLiquidity() public {
    int8 bin = 4;
    uint104 shares = 999;

    vm.expectRevert(
      abi.encodeWithSelector(
        IMetricOmmPoolActions.MinimalLiquidity.selector, uint256(uint104(shares)), uint256(MINIMAL_MINTABLE_LIQUIDITY)
      )
    );
    _callAddLiquidity(USER_INDEX, DEFAULT_SALT, _createDeltaArray(bin, shares));
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
