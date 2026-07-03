Audit Report

## Title
Missing Chainlink Price Feed Staleness Check Allows Stale Prices to Over-Mint rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and discards all return values except `price`, performing no staleness validation on `updatedAt`. Because this oracle directly prices LST assets used in rsETH minting, a stale Chainlink feed reporting a pre-depeg price allows any depositor to receive more rsETH than their contributed assets are worth, diluting existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 silently discards `updatedAt`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

There is no comparison of `block.timestamp - updatedAt` against any heartbeat threshold. The stale price propagates directly into two critical flows:

**Flow 1 — rsETH minting** (`LRTDepositPool.getRsETHAmountToMint`, line 520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**Flow 2 — rsETH price update** (`LRTOracle._getTotalEthInProtocol`, lines 339–343):
```solidity
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

The `pricePercentageLimit` guard in `_updateRsETHPrice()` (lines 252–266) is not a sufficient mitigation: it only applies when `updateRSETHPrice()` is called and only triggers if the price change exceeds the configured threshold. It does not gate individual deposit calls, and a small depeg (e.g., 1–3%) may not exceed the threshold at all. The deposit path at `LRTDepositPool.depositAsset` / `depositETH` calls `getRsETHAmountToMint` directly, which makes a live call to `ChainlinkPriceOracle.getAssetPrice()` with no staleness gate.

Additionally, `price` is cast directly to `uint256` without a `price > 0` check. A zero return produces `rsethAmountToMint = 0`, causing a depositor to lose their LST with no rsETH minted if `minRSETHAmountExpected` is also 0.

## Impact Explanation
**High — Theft of unclaimed yield.**

When a Chainlink LST/ETH feed goes stale at a price above the current true market price (e.g., during a depeg event), any depositor calling `depositAsset()` receives:

```
rsethAmountToMint = (depositAmount × staleHighPrice) / rsETHPrice
```

The excess rsETH minted represents a claim on ETH backing that belongs to existing rsETH holders. The depositor can immediately hold or redeem this over-minted rsETH, extracting value (unclaimed yield and principal backing) from existing holders. This is a direct, quantifiable transfer of value from existing rsETH holders to the depositor, matching the "Theft of unclaimed yield" impact class.

## Likelihood Explanation
Chainlink LST/ETH feeds (stETH/ETH, rETH/ETH, ETHx/ETH, etc.) have documented heartbeat windows of up to 24 hours on Ethereum mainnet. On L2s (Arbitrum, Optimism, Base — all networks where this protocol is deployed), sequencer downtime or gas spikes can cause feeds to miss their heartbeat. No attacker action is required beyond a normal `depositAsset()` call during a staleness window that coincides with a price movement. The protocol holds multiple LST assets, each with its own feed, increasing the probability that at least one feed is stale at any given time. This is a realistic, non-adversarial scenario.

## Recommendation
1. **Add a per-feed staleness threshold** stored in a mapping (e.g., `staleFeedThreshold[asset]`) and revert if `block.timestamp - updatedAt > staleFeedThreshold[asset]`.
2. **Add a non-positive price guard**: `require(price > 0, "Invalid price")`.
3. **For L2 deployments**, add a Chainlink L2 sequencer uptime feed check before consuming any price data.

Example fix:
```solidity
(uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(price > 0, "Invalid price");
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt <= staleFeedThreshold[asset], "Stale price");
```

## Proof of Concept
1. A Chainlink stETH/ETH feed misses its 24-hour heartbeat. Last stored answer: `1.0e18` (pre-depeg).
2. Actual stETH price drops to `0.97e18` due to a depeg event; the feed is not updated.
3. An attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
4. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.0e18` (stale).
5. `rsethAmountToMint = (100e18 × 1.0e18) / rsETHPrice` — attacker receives rsETH as if stETH = 1 ETH, having contributed assets worth only 97 ETH.
6. The 3 ETH difference in backing is extracted from existing rsETH holders.

**Foundry fork test plan:**
- Fork Ethereum mainnet at a block where a stETH/ETH Chainlink feed answer is known.
- Deploy a mock `AggregatorV3Interface` that returns a fixed stale `updatedAt` (e.g., `block.timestamp - 25 hours`) and a price above the current true price.
- Register the mock feed via `ChainlinkPriceOracle.updatePriceFeedFor`.
- Call `LRTDepositPool.depositAsset` and assert that `rsethAmountToMint` exceeds the fair value, confirming over-minting with no revert.