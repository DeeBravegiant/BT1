### Title
Missing Arbitrum/Optimism Sequencer Uptime Check in Chainlink Price Feed - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` on Chainlink price feeds without verifying that the L2 sequencer is live. When deployed on Arbitrum or Optimism (both confirmed deployment targets), a sequencer outage causes Chainlink feeds to return stale prices. Any caller can invoke `LRTOracle.updateRSETHPrice()` immediately after the sequencer resumes — before Chainlink updates its feeds — locking in a stale rsETH price that can be exploited for deposits or withdrawals.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the asset price directly from Chainlink with no sequencer check and no staleness validation:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

This price is consumed by `LRTOracle._getTotalEthInProtocol()`, which iterates over all supported assets and prices them using `getAssetPrice()`: [2](#0-1) 

`_getTotalEthInProtocol()` is called by `_updateRsETHPrice()`, which is triggered by the public `updateRSETHPrice()` function: [3](#0-2) 

The protocol is confirmed to be deployed on Arbitrum and Optimism: [4](#0-3) 

On L2 chains, when the sequencer goes offline, Chainlink oracles stop updating but continue to return the last known price. After the sequencer resumes, there is a window (up to the Chainlink heartbeat interval, e.g., 1 hour for ETH/USD) during which `latestRoundData()` still returns the pre-downtime stale price. No sequencer uptime feed (e.g., Chainlink's `0x4da69F028a5790fA2...` on Arbitrum) is consulted anywhere in the oracle path.

### Impact Explanation

An attacker can call `updateRSETHPrice()` immediately after the sequencer resumes, before Chainlink refreshes its feeds. If the stale price is lower than the true current price (e.g., assets appreciated during downtime), the rsETH price is set too low, allowing the attacker to deposit and receive more rsETH than deserved — effectively stealing value from existing holders. Conversely, a stale price higher than reality allows the attacker to withdraw more ETH than their rsETH is worth. This constitutes temporary mispricing that enables direct theft of user funds or yield.

**Impact: Medium — Temporary freezing of funds / theft of unclaimed yield via oracle manipulation during sequencer recovery window.**

### Likelihood Explanation

Arbitrum sequencer outages have occurred historically (e.g., December 2022, June 2023). The `updateRSETHPrice()` function is publicly callable with no access control, so any attacker can race to call it in the sequencer recovery window. No additional preconditions are required.

### Recommendation

Add a sequencer uptime check before consuming Chainlink data on L2 deployments, following Chainlink's documented pattern:

```solidity
// Example: Arbitrum sequencer uptime feed
AggregatorV2V3Interface sequencerUptimeFeed = AggregatorV2V3Interface(SEQUENCER_UPTIME_FEED);
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
// 0 = sequencer up, 1 = sequencer down
require(answer == 0, "Sequencer down");
// Enforce a grace period after sequencer recovery
require(block.timestamp - startedAt > GRACE_PERIOD_TIME, "Grace period not over");
```

Also add a staleness check on `updatedAt` from `latestRoundData()` against a configured heartbeat threshold.

### Proof of Concept

1. Protocol is live on Arbitrum with, e.g., stETH priced at 1.05 ETH via `ChainlinkPriceOracle`.
2. Arbitrum sequencer goes offline. stETH appreciates to 1.07 ETH on mainnet, but the Chainlink feed on Arbitrum is frozen at 1.05 ETH.
3. Sequencer comes back online. Chainlink feed still reads 1.05 ETH (within heartbeat window).
4. Attacker immediately calls `LRTOracle.updateRSETHPrice()`. `_getTotalEthInProtocol()` prices stETH at 1.05 ETH (stale), computing a lower-than-true rsETH price.
5. Attacker deposits stETH and receives more rsETH than the true exchange rate warrants, at the expense of existing rsETH holders.
6. Chainlink updates to 1.07 ETH. Next `updateRSETHPrice()` call raises rsETH price, but the attacker already holds excess rsETH minted at the stale rate.

The root cause is solely in `ChainlinkPriceOracle.getAssetPrice()` at line 52 — no sequencer check, no `updatedAt` staleness guard — making it a necessary and sufficient vulnerable step in the attack path. [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** README.md (L841-843)
```markdown
| Arbitrum     | 0xe119D214a6efa7d3cF60e6E59481EDe1B0064A6B     |
| Optimism     | 0x68A9EC5b93F04a60c77F486a664f283B2E4E2B72     |
| BSC          | 0x4186BFC76E2E237523CBC30FD220FE055156b41F     |
```
