Audit Report

## Title
Stale Cross-Chain rsETH/ETH Rate Used in L2 Pool Deposits Without Staleness Check — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver` stores `lastUpdated` on every rate update but `getRate()` returns `rate` unconditionally without checking it. All L2 pool contracts (`RSETHPoolV2NBA`, `RSETHPoolV2`, `RSETHPoolV3`, etc.) delegate pricing entirely to this call, so a stale (lower) rate causes over-minting of wrsETH per ETH deposited, diluting the backing of existing wrsETH holders and constituting theft of their unclaimed yield.

## Finding Description
`CrossChainRateReceiver.lzReceive()` records both `rate` and `lastUpdated` on every LayerZero message:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L95-97
rate = _rate;
lastUpdated = block.timestamp;
```

However, `getRate()` returns `rate` with no reference to `lastUpdated`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L102-105
function getRate() external view returns (uint256) {
    return rate;
}
```

Every L2 pool delegates pricing to this call. `RSETHPoolV2NBA.viewSwapRsETHAmountAndFee()`:

```solidity
// contracts/pools/RSETHPoolV2NBA.sol L129-132
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

The same pattern is present in `RSETHPoolV2`, `RSETHPoolV3`, `RSETHPool`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`. The rate is pushed from mainnet by an off-chain bot calling `CrossChainRateProvider.updateRate()`, which sends a LayerZero message. If the bot fails or LayerZero delivery is delayed, `CrossChainRateReceiver.rate` becomes stale. Because rsETH accrues yield continuously, the true rate rises above the stale stored rate. A depositor calling `deposit()` with the stale (lower) rate receives more wrsETH than the current backing justifies, permanently diluting existing holders.

The contrast with `ChainlinkOracleForRSETHPoolCollateral.getRate()` confirms the protocol is aware of staleness risks and has addressed them elsewhere:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30
if (answeredInRound < roundID) revert StalePrice();
```

No equivalent guard exists in `CrossChainRateReceiver.getRate()`.

## Impact Explanation
**High — Theft of unclaimed yield.** When the stored rate is stale and lower than the true rate, `rsETHAmount = amountAfterFee * 1e18 / staleRate` mints more wrsETH than the deposited ETH justifies. Once the rate updates, the attacker's over-minted wrsETH is fully backed at the correct rate, having extracted accrued yield from prior depositors. The excess wrsETH is retained permanently. This matches the allowed impact "Theft of unclaimed yield."

## Likelihood Explanation
The update path has two off-chain dependencies: the bot must call `updateRate()` on mainnet, and LayerZero must deliver the message to the L2 receiver. Either can fail due to bot bugs, gas exhaustion, network congestion, or LayerZero infrastructure issues. rsETH yield accrues every Ethereum epoch (~6.4 minutes), so even a few hours of staleness creates a measurable exploitable gap. An attacker can monitor `CrossChainRateReceiver.lastUpdated` on-chain and act whenever the gap between the stale rate and the live mainnet `LRTOracle.rsETHPrice` is profitable. The attack is repeatable across all deployed L2 chains (Arbitrum, Optimism, Polygon zkEVM, Blast, Mode, Scroll).

## Recommendation
Add a configurable `maxStaleness` threshold to `CrossChainRateReceiver` and revert in `getRate()` if the rate is stale:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    if (block.timestamp - lastUpdated > maxStaleness) revert StaleRate();
    return rate;
}
```

This mirrors the staleness guard already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()`.

## Proof of Concept
1. Fork an L2 (e.g., Arbitrum) where `RSETHRateReceiver` is deployed.
2. Record `CrossChainRateReceiver.rate` = `1.03e18` and `lastUpdated` = some past timestamp.
3. Advance block timestamp by 12 hours (simulating bot failure) without calling `lzReceive`.
4. Confirm mainnet `LRTOracle.rsETHPrice` has increased to `1.05e18`.
5. Call `RSETHPoolV2NBA.deposit{value: 100 ether}("")`.
6. `viewSwapRsETHAmountAndFee(100e18)` computes: `rsETHAmount = 100e18 * 1e18 / 1.03e18 ≈ 97.09 wrsETH`.
7. Correct amount at live rate: `100e18 * 1e18 / 1.05e18 ≈ 95.24 wrsETH`.
8. Attacker receives `≈1.85 excess wrsETH` at the expense of existing holders.
9. Deliver the LayerZero message (call `lzReceive` with `_rate = 1.05e18`); attacker's wrsETH is now fully backed, yield extracted from prior depositors.

Foundry fork test: use `vm.warp` to advance time, mock `lzReceive` to skip the update, call `deposit`, assert minted amount exceeds `100e18 * 1e18 / 1.05e18`.