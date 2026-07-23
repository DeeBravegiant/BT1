Audit Report

## Title
USDC-Blacklisted LP Address Permanently Locks Principal in `removeLiquidity` — (File: `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`LiquidityLib.removeLiquidity` uses a push-transfer pattern, sending owed tokens directly to `owner` via `safeTransfer`. If `owner` is on the USDC (or USDT) blacklist, every call to `removeLiquidity` reverts, permanently locking the LP's principal in the pool. No alternative withdrawal path, recipient parameter, or admin rescue mechanism exists.

## Finding Description
`MetricOmmPool.removeLiquidity` enforces `msg.sender == owner` at line 206 and delegates to `LiquidityLib.removeLiquidity`. Inside the library, bin-state accounting updates occur first (lines 210–214: `binState.token0BalanceScaled`, `binState.token1BalanceScaled`, `binTotalShares[binIdx]`, `positionBinShares[posKey]`), followed by push transfers at lines 242–247:

```solidity
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
if (amount1Removed > 0) {
    IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
}
```

Because `safeTransfer` propagates a revert from USDC's blacklist check, the entire transaction rolls back — including all accounting updates. The LP's shares remain intact in `_positionBinShares` and `_binTotalShares`, but every future call to `removeLiquidity` will revert identically. The `msg.sender == owner` guard at line 206 eliminates any workaround using a different caller or recipient. `removeLiquidity` carries no `whenNotPaused` modifier (unlike `swap` at line 224), so pausing the pool provides no relief. There is no pull-claim mapping, no `recipient` parameter, and no admin rescue path anywhere in the pool.

## Impact Explanation
An LP whose address is added to the USDC blacklist after depositing liquidity permanently loses access to their principal. The pool's bin balances correctly reflect the owed amounts, but those amounts can never be transferred out. This constitutes a direct, irrecoverable loss of user principal. USDC/USDT non-standard behavior is explicitly in scope per contest rules, satisfying the allowed impact gate for Medium/High direct loss of user principal.

## Likelihood Explanation
USDC blacklisting requires USDC Centre to act against the LP's address (e.g., regulatory freeze or sanctions linkage). This is a low-probability external event. Combined with the high impact (total loss of principal), overall severity is **Medium** under Sherlock criteria (low likelihood × high impact).

## Recommendation
Replace the push-transfer pattern with a pull-claim pattern: accumulate owed amounts in a per-address mapping (e.g., `mapping(address => uint256) public claimable0 / claimable1`) during `removeLiquidity`, and expose a separate `claimTokens(address recipient)` function allowing the owner to direct proceeds to any non-blacklisted address. Alternatively, add a `recipient` parameter to `removeLiquidity` (distinct from `owner`) so the LP can specify a non-blacklisted destination at withdrawal time, mirroring the `recipient` parameter already used in `swap`.

## Proof of Concept
1. Pool is deployed with USDC as `token0`.
2. LP calls `addLiquidity(owner=LP_ADDR, ...)` — shares are minted, USDC enters the pool.
3. USDC Centre blacklists `LP_ADDR`.
4. LP calls `removeLiquidity(owner=LP_ADDR, ...)`.
5. `LiquidityLib.removeLiquidity` computes `amount0Removed > 0` and calls `IERC20(USDC).safeTransfer(LP_ADDR, amount0Removed)` at line 243.
6. USDC's `transfer` reverts because `LP_ADDR` is blacklisted; the entire transaction rolls back.
7. LP's shares remain in `_positionBinShares`; step 4–6 repeats on every future attempt.
8. The `msg.sender != owner` guard at line 206 prevents any alternative caller from redirecting proceeds — LP principal is permanently locked.