Audit Report

## Title
Stale `rsETHPrice` Used in `depositETH`/`depositAsset` Allows Depositors to Mint Excess rsETH - (File: contracts/LRTDepositPool.sol)

## Summary

`LRTDepositPool.depositETH()` and `depositAsset()` compute the rsETH mint amount by dividing the deposit value by the cached `LRTOracle.rsETHPrice` storage variable without first refreshing it via `updateRSETHPrice()`. Because rsETH is a yield-bearing token whose price monotonically increases as staking rewards accrue, any window between oracle updates leaves the cached price stale (lower than actual), causing the mint calculation to produce more rsETH than the deposit warrants and diluting all existing rsETH holders.

## Finding Description

`depositETH()` calls `_beforeDeposit()` → `getRsETHAmountToMint()`, which computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` is a storage variable updated only when `updateRSETHPrice()` is explicitly called. That function is public and permissionless (gated only by `whenNotPaused`):

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Neither `depositETH()` nor `depositAsset()` invokes `updateRSETHPrice()` or its internal equivalent before computing the mint amount. Because `rsETHPrice` appears in the denominator, a stale (lower) value inflates `rsethAmountToMint`. The `minRSETHAmountExpected` slippage guard does not protect against this: the attacker sets it to the inflated amount they expect to receive, so it never reverts.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` worsens the window: if the true price has risen above the daily threshold, a public call to `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold` for non-managers, meaning the stale price persists until a manager acts, giving the attacker more time to exploit.

## Impact Explanation

**High — Theft of unclaimed yield.** Every deposit made against a stale `rsETHPrice` mints excess rsETH. This excess dilutes all existing rsETH holders: their proportional claim on the protocol's ETH TVL shrinks without any reduction in their token balance. The attacker's profit equals the yield accrued since the last `updateRSETHPrice()` call, scaled by deposit size. With a large deposit and a multi-day staleness window (especially when the `pricePercentageLimit` gate blocks public updates), the stolen yield is material and directly matches the "Theft of unclaimed yield" impact class.

## Likelihood Explanation

- `updateRSETHPrice()` is called by the operator on a periodic schedule, not atomically with every deposit. Any gap between calls is exploitable.
- The attack requires no special role — any address that can call `depositETH()` or `depositAsset()` can exploit it.
- The attacker can trivially detect the opportunity by comparing `LRTOracle.rsETHPrice` against the live TVL returned by `LRTDepositPool.getTotalAssetDeposits()` and `IRSETH.totalSupply()`.
- The `pricePercentageLimit` guard can extend the exploitable window: if the price has risen above the daily threshold, only a manager can call `updateRSETHPriceAsManager()`, meaning the stale price persists until the manager acts.

## Recommendation

At the start of `depositETH()` and `depositAsset()` in `LRTDepositPool`, call `ILRTOracle(lrtOracleAddress).updateRSETHPrice()` (or invoke the internal `_updateRsETHPrice()` equivalent) before computing the mint amount. This ensures the price used for minting always reflects the current TVL, eliminating the staleness window. If the `pricePercentageLimit` gate would revert a public update, the deposit should also revert, preventing minting against a price that cannot be safely refreshed.

## Proof of Concept

1. At time T, the operator calls `updateRSETHPrice()`. `LRTOracle.rsETHPrice` is set to `1.05e18`.
2. Staking rewards accrue over 24 hours. The true price rises to `1.06e18`, but `rsETHPrice` remains `1.05e18`.
3. Alice calls `depositETH{value: 100 ether}(0, "")`.
4. `getRsETHAmountToMint` computes: `100e18 * 1e18 / 1.05e18 ≈ 95.238 rsETH`.
5. The correct amount at the live price: `100e18 * 1e18 / 1.06e18 ≈ 94.340 rsETH`.
6. Alice receives ≈ 0.898 rsETH excess, stealing yield from existing holders.
7. Alice (or anyone) then calls `updateRSETHPrice()` to advance the price to `1.06e18`, locking in the dilution.

**Foundry fork test plan:** Fork mainnet, snapshot `rsETHPrice`, advance time to simulate reward accrual (or directly manipulate TVL), call `depositETH` without calling `updateRSETHPrice` first, assert that `rsethAmountToMint` exceeds `100e18 * 1e18 / livePrice`, then call `updateRSETHPrice` and assert the price increased, confirming dilution of pre-existing holders.