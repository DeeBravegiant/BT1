Audit Report

## Title
LP principal permanently frozen when position owner is blacklisted by USDC/USDT — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`MetricOmmPool.removeLiquidity` enforces `msg.sender == owner` and passes `owner` as the sole transfer destination to `LiquidityLib.removeLiquidity`, which hardcodes `safeTransfer(owner, ...)` with no `recipient` override. If the position owner is blacklisted by USDC or USDT after depositing, every withdrawal attempt reverts at the transfer step, permanently locking the LP's principal in the pool with no recovery path.

## Finding Description
`MetricOmmPool.removeLiquidity` (L206) enforces:
```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [1](#0-0) 

It then delegates to `LiquidityLib.removeLiquidity` (L208–210), passing `owner` as the only address: [2](#0-1) 

Inside `LiquidityLib.removeLiquidity`, after burning shares and computing owed amounts, the transfer is hardcoded to `owner` (L242–247):
```solidity
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
if (amount1Removed > 0) {
    IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
}
``` [3](#0-2) 

The function signature accepts no `recipient` parameter: [4](#0-3) 

The interface NatSpec confirms the design intent — tokens go directly to `owner` with no alternative: [5](#0-4) 

A grep across all production contracts for any position transfer, delegate-removal, emergency-withdrawal, or rescue mechanism returns no results — there is no alternative withdrawal path. By contrast, `swap` already decouples caller from destination via a `recipient` parameter (L217–224): [6](#0-5) 

When USDC or USDT blacklists `owner`, `safeTransfer(owner, ...)` reverts. Because the revert rolls back all state changes (share burns, bin balance updates), the position remains recorded in storage but is permanently unwithdrawable. The underlying tokens are locked in the pool contract forever.

## Impact Explanation
Direct, permanent loss of LP principal. The affected LP's deposited USDC or USDT is irrecoverable: shares remain in storage but can never be redeemed because every `removeLiquidity` call reverts at the transfer step. This satisfies the allowed-impact gate item "Critical/High/Medium direct loss of user principal… above Sherlock thresholds." Severity is **Medium** (low-probability event, high-impact outcome — total loss of position principal with no recovery path).

## Likelihood Explanation
USDC blacklisting (Circle) and USDT blacklisting (Tether) are real, documented, on-chain mechanisms exercised in response to regulatory orders, sanctions, and exploit responses. No attacker capability is required — the freeze is imposed by the token issuer. The exposure window is the entire lifetime of an LP position, which can be indefinite. Any LP holding a position in a USDC/USDT pool is at risk for the duration of their deposit.

## Recommendation
Add an optional `recipient` parameter to `removeLiquidity`, mirroring the existing `swap` design:

```solidity
// MetricOmmPool.sol
function removeLiquidity(
    address owner,
    address recipient,   // new: token destination, defaults to owner
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) {
    if (msg.sender != owner) revert NotPositionOwner();
    ...
    LiquidityLib.removeLiquidity(
        _liquidityContext(), owner, recipient, salt, deltas, ...
    );
}

// LiquidityLib.sol
if (amount0Removed > 0) IERC20(ctx.token0).safeTransfer(recipient, amount0Removed);
if (amount1Removed > 0) IERC20(ctx.token1).safeTransfer(recipient, amount1Removed);
```

This preserves the `msg.sender == owner` authorization check while allowing the owner to redirect token output to a non-blacklisted address.

## Proof of Concept
1. Alice (`owner = 0xAlice`) calls `addLiquidity` via `MetricOmmPoolLiquidityAdder`, depositing 100,000 USDC into bin 0.
2. USDC Centre blacklists `0xAlice` (regulatory freeze or sanctions).
3. Alice calls `removeLiquidity(0xAlice, salt, deltas, "")`.
4. `MetricOmmPool` passes the call to `LiquidityLib.removeLiquidity` with `owner = 0xAlice`.
5. Shares are burned in memory, `amount0Removed = 100,000 USDC` is computed.
6. `IERC20(USDC).safeTransfer(0xAlice, 100_000e6)` reverts — USDC blacklist check fails.
7. The entire transaction reverts; Alice's shares remain in `_positionBinShares`, her 100,000 USDC is permanently locked in the pool.
8. No alternative function (`transferPosition`, `emergencyWithdraw`, `rescue`, etc.) exists in the production contracts to redirect the transfer.

**Foundry fork test outline:**
```solidity
// Fork mainnet, deploy pool with USDC/USDT
// Alice adds liquidity
// Prank USDC blacklister, blacklist Alice
// vm.prank(alice); pool.removeLiquidity(alice, salt, deltas, "");
// Assert: reverts with USDC transfer failure
// Assert: alice's positionBinShares unchanged (principal locked)
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L208-210)
```text
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-170)
```text
  function removeLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Removed, uint256 amount1Removed) {
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-247)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L164-174)
```text
  /// @notice Burn shares across bins for `(owner, salt)` and send underlying tokens to `owner`.
  /// @dev Requires `msg.sender == owner` (`NotPositionOwner` otherwise). No callback: tokens are transferred out directly.
  /// @param owner Must equal `msg.sender`.
  /// @param salt Position salt with `owner`.
  /// @param deltas Parallel arrays of bins and share burns.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeRemoveLiquidity / afterRemoveLiquidity).
  /// @return amount0Removed Total token0 sent from the pool to `owner` (native).
  /// @return amount1Removed Total token1 sent from the pool to `owner` (native).
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    returns (uint256 amount0Removed, uint256 amount1Removed);
```
