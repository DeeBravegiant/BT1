Audit Report

## Title
Protocol Fee Charged on Appreciation of Committed Withdrawal Assets Inflates Fee Base, Stealing Yield from rsETH Holders — (File: contracts/LRTOracle.sol)

## Summary
In `LRTOracle._updateRsETHPrice()`, the protocol performance fee is computed on the full `totalETHInProtocol`, which includes `assetLyingUnstakingVault` — assets already committed to withdrawers at a fixed `expectedAssetAmount` and sitting idle in `LRTUnstakingVault`. When those committed assets appreciate (e.g., stETH rebase while held in the vault), the protocol charges a fee on that appreciation even though the withdrawers' payouts are fixed. The appreciation surplus belongs to remaining rsETH holders but is partially diverted to the protocol treasury via fee minting, diluting all rsETH holders.

## Finding Description

`_updateRsETHPrice()` computes the reward amount as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
// ...
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

`rsethSupply` is `IRSETH(rsETHTokenAddress).totalSupply()`, which includes rsETH transferred to `LRTWithdrawalManager` by `initiateWithdrawal` but not yet burned. `totalETHInProtocol` is assembled by `_getTotalEthInProtocol()`, which calls `getTotalAssetDeposits(asset)` for every supported asset. `getTotalAssetDeposits` sums all buckets including `assetLyingUnstakingVault`:

```solidity
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
        + assetLyingUnstakingVault);
```

For LSTs, `assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault)` — the raw ERC-20 balance of `LRTUnstakingVault`. For ETH, it is `lrtUnstakingVault.balance`.

`LRTUnstakingVault` holds assets moved there by operators to service pending withdrawal requests. Those assets are committed to withdrawers via `assetsCommitted[asset]` in `LRTWithdrawalManager`, set at `initiateWithdrawal` time:

```solidity
assetsCommitted[asset] += expectedAssetAmount;
```

At `unlockQueue` time, `_calculatePayoutAmount` returns `min(originalExpectedAmount, currentReturn)`, so if the asset appreciated, the user still receives only `originalExpectedAmount`. The surplus remains in the vault. The rsETH corresponding to the withdrawal is burned at `unlockQueue`, not at `initiateWithdrawal`.

Because both sides of `rewardAmount = totalETHInProtocol - previousTVL` include the committed assets (assets in the vault on the left; rsETH in the withdrawal manager contributing to `previousTVL` on the right), any appreciation of those committed assets between price updates is included in `rewardAmount`. The protocol then charges `protocolFeeInBPS` on this appreciation — even though the withdrawers' payouts are fixed and the appreciation surplus accrues to remaining rsETH holders via the price mechanism. The fee minting dilutes all rsETH holders without them receiving the corresponding yield.

The existing `pricePercentageLimit` guard only prevents excessive price jumps; it does not distinguish between appreciation of actively staked assets and appreciation of committed assets. No other check excludes committed assets from the fee base.

## Impact Explanation

The protocol performance fee is charged on appreciation of assets that are committed to withdrawers at a fixed price. That appreciation is yield that rightfully belongs to remaining rsETH holders (it increases `totalETHInProtocol` and thus the rsETH price). Instead, a portion is diverted to the protocol treasury via rsETH fee minting, diluting all rsETH holders. Withdrawers are unaffected (they receive their fixed `expectedAssetAmount`), but remaining rsETH holders receive less yield than they are entitled to.

**Impact: High — Theft of unclaimed yield from rsETH holders.**

## Likelihood Explanation

`updateRSETHPrice()` is a public, permissionless function callable by any external account. The condition triggers whenever: (1) assets are present in `LRTUnstakingVault` (routine during any withdrawal processing period), and (2) TVL has increased since the last update (routine during stETH rebase or ETH staking reward accrual). Both conditions are continuously present during normal protocol operation whenever withdrawals are in flight. No privileged role or special setup is required beyond the normal withdrawal lifecycle.

**Likelihood: Medium** — requires assets in the unstaking vault (common) and TVL growth (routine).

## Recommendation

Subtract the ETH-denominated value of `assetLyingUnstakingVault` from `totalETHInProtocol` before computing `rewardAmount`, so the fee base only reflects appreciation of actively staked assets. In `_updateRsETHPrice()` (or in a helper), compute a `committedAssetsInETH` sum across all supported assets using the same price oracle, and deduct it:

```solidity
uint256 rewardAmount = (totalETHInProtocol - committedAssetsInETH) - previousTVL;
```

Alternatively, expose a separate `_getTotalCommittedAssetsInETH()` private function that mirrors `_getTotalEthInProtocol()` but sums only `assetLyingUnstakingVault` per asset, and subtract it from `totalETHInProtocol` before the fee calculation. This ensures fees are only charged on assets that are actively earning yield for the protocol.

## Proof of Concept

1. Protocol state: 1,000 ETH TVL, 1,000 rsETH supply, rsETH price = 1 ETH. 100 ETH worth of stETH is staked.
2. User calls `initiateWithdrawal(stETH, 100 rsETH)`. rsETH is transferred to `LRTWithdrawalManager` (not burned). `assetsCommitted[stETH] = 100 stETH`. `expectedAssetAmount = 100 stETH` is locked.
3. Operator calls `transferAssetToLRTUnstakingVault(stETH, 100 stETH)`. 100 stETH now sits in `LRTUnstakingVault`. `assetLyingUnstakingVault = 100 stETH`.
4. stETH rebases: 100 stETH → 100.1 stETH. `totalETHInProtocol` increases by 0.1 ETH.
5. Anyone calls `updateRSETHPrice()`:
   - `totalETHInProtocol = 1,000.1 ETH` (includes 100.1 stETH in unstaking vault)
   - `previousTVL = 1,000 rsETH × 1 ETH = 1,000 ETH`
   - `rewardAmount = 0.1 ETH`
   - `protocolFeeInETH = 0.1 ETH × 10% = 0.01 ETH` (example 10% fee)
   - 0.01 ETH worth of rsETH minted to treasury; rsETH price set to `(1,000.1 − 0.01) / 1,000 = 1.00009 ETH`
6. At `unlockQueue`: `_calculatePayoutAmount` returns `min(100 stETH, 100.009 stETH) = 100 stETH`. The withdrawer receives exactly 100 stETH. The 0.1 stETH rebase gain belonged to remaining rsETH holders, but 10% of it (0.01 ETH) was taken as a protocol fee on assets that were not earning for the protocol — they were committed to a withdrawer at a fixed price.

A Foundry fork test can reproduce this by: (a) setting up the protocol state with a pending withdrawal and assets in `LRTUnstakingVault`, (b) simulating a stETH rebase by directly increasing the stETH balance of the vault, (c) calling `updateRSETHPrice()` and asserting that `protocolFeeInETH > 0` and that rsETH was minted to treasury, and (d) confirming the withdrawer still receives only `expectedAssetAmount`.