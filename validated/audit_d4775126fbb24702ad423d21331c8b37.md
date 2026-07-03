Audit Report

## Title
Missing Staleness Check on Cross-Chain Rate Enables Excess wrsETH Minting During LayerZero Delays - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` without validating `lastUpdated`, meaning any period of LayerZero message delay leaves a stale (lower-than-true) rate on L2. All L2 pool variants use this rate as the denominator when computing wrsETH to mint, so depositors during a stale window receive more wrsETH than their ETH entitles them to, diluting the accrued yield of pre-existing rsETH holders.

## Finding Description

`CrossChainRateReceiver` records `lastUpdated` on every `lzReceive` call but never reads it in `getRate()`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L95-104
rate = _rate;
lastUpdated = block.timestamp;   // written, never read in getRate()
...
function getRate() external view returns (uint256) {
    return rate;                 // no staleness check
}
```

`RSETHPoolV3.viewSwapRsETHAmountAndFee` uses the returned rate as the denominator:

```solidity
// contracts/pools/RSETHPoolV3.sol L304-307
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Because rsETH/ETH monotonically increases as EigenLayer rewards accrue, a stale rate is always lower than the true rate. A lower denominator produces a larger `rsETHAmount`, so every depositor during the stale window receives more wrsETH than they are entitled to. The same pattern is present in `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee` (L418-427). The `dailyMintLimit` modifier caps total daily issuance but does not prevent the per-deposit over-issuance caused by the stale rate.

`MultiChainRateProvider.updateRate()` is permissionless (L108), but it requires a live LayerZero path and sufficient ETH for gas — both of which can be unavailable simultaneously during congestion.

## Impact Explanation

**High — Theft of unclaimed yield.**

The rsETH/ETH rate encodes all staking yield accrued to existing holders. When the L2 rate is stale, new depositors receive wrsETH shares priced at the old (lower) rate. When those shares are later redeemed on L1 via the wrapper, the excess claim is satisfied from the yield pool owed to pre-existing holders. At $100M TVL with 0.5% rate drift over a 7-day outage, the over-issuance is approximately $500K in stolen yield. The impact scales linearly with both staleness duration and deposit volume during the outage window.

## Likelihood Explanation

LayerZero has experienced documented message delivery delays. No attacker capability is required beyond calling the public `deposit()` function during a period when LayerZero messages from L1 are delayed. The scenario does not require oracle manipulation, privileged access, or governance capture — only a network congestion event that prevents timely `lzReceive` delivery. The SECURITY.md exclusion for "incorrect data supplied by third-party oracles" does not apply here: the issue is the protocol's own contract failing to validate the age of its stored state, not incorrect data from an external oracle.

## Recommendation

1. Add a staleness check in `CrossChainRateReceiver.getRate()`:
   ```solidity
   uint256 public constant MAX_RATE_AGE = 24 hours;
   function getRate() external view returns (uint256) {
       require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate stale");
       return rate;
   }
   ```
2. Add a minimum-rate floor (`require(rate >= 1e18)`) since rsETH/ETH should never fall below 1:1.
3. Mirror the downside-protection pause already present in `LRTOracle._updateRsETHPrice()` on L1: automatically pause L2 deposits if the rate has not been updated within the staleness window.

## Proof of Concept

1. At T=0, `lzReceive` sets `rate = 1.05e18`, `lastUpdated = T`.
2. LayerZero experiences a 48-hour delay. L1 rate grows to `1.06e18`. L2 `rate` remains `1.05e18`.
3. Any depositor calls `RSETHPoolV3.deposit{value: 100 ether}("")`.
4. `viewSwapRsETHAmountAndFee(100e18)` computes: `rsETHAmount = 100e18 * 1e18 / 1.05e18 = 95.238e18`.
5. Correct amount at true rate: `100e18 * 1e18 / 1.06e18 = 94.339e18`.
6. Excess minted per 100 ETH: `0.899e18 wrsETH` (~0.95% over-issuance).
7. No revert occurs; `getRate()` returns the stale value without any staleness check; `dailyMintLimit` is consumed at the inflated amount but does not block the transaction.

**Foundry fork test plan**: Fork an L2 deployment, call `lzReceive` to set an initial rate, advance `block.timestamp` by 48 hours without calling `lzReceive` again, then call `deposit` and assert that `wrsETH.balanceOf(depositor)` exceeds `deposit_amount * 1e18 / true_rate` by the expected drift amount.