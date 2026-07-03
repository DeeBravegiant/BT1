### Title
No Chainlink Staleness Check Allows Stale Price to Corrupt rsETH Exchange Rate — (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards every return value except `price`. There is no check on `updatedAt`, no heartbeat comparison, and no `answeredInRound` guard. A stale Chainlink feed will be consumed as if it were fresh, corrupting the rsETH exchange rate used for all deposits and withdrawals.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` reads the Chainlink feed as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The four other return values — `roundId`, `startedAt`, `updatedAt`, and `answeredInRound` — are all discarded. No staleness window (heartbeat) is enforced. If the Chainlink sequencer or oracle network stops updating a feed (e.g., during congestion, a network incident, or a feed deprecation), the last recorded price is returned indefinitely without any revert or warning.

This oracle is the `IPriceFetcher` implementation registered in `LRTOracle` for each supported LST asset. `LRTOracle._updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which aggregates asset balances weighted by prices from this oracle, and uses the result to compute and store `rsETHPrice`. That stored price governs both minting (deposits) and redemption (withdrawals).

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` at least checks `answeredInRound < roundID` and `timestamp == 0`, but `ChainlinkPriceOracle` has no equivalent guard whatsoever.

---

### Impact Explanation

**High — Theft of user funds / protocol insolvency.**

If a supported LST feed goes stale while the real market price has fallen:

1. `getAssetPrice()` returns the old (inflated) price.
2. `_updateRsETHPrice()` computes a `totalETHInProtocol` that is higher than the actual on-chain value.
3. `rsETHPrice` is set above its true value.
4. Any user who redeems rsETH at this inflated rate withdraws more ETH than the protocol actually holds for their share, draining funds from remaining holders and pushing the protocol toward insolvency.

The reverse scenario (stale price below actual) causes depositors to receive excess rsETH, diluting existing holders — theft of unclaimed yield at minimum.

---

### Likelihood Explanation

Chainlink feeds can and do go stale: sequencer downtime on L2s, feed deprecations, or extreme network congestion are documented real-world events. The protocol supports multiple LST assets, each with its own feed and heartbeat. Any single feed going stale is sufficient to trigger the issue. No privileged access is required; any user calling `updateRSETHPrice()` (a public function) or depositing/withdrawing triggers the price update path.

---

### Recommendation

Add a configurable `maxStaleness` (heartbeat) per asset feed, and revert if `block.timestamp - updatedAt > maxStaleness`:

```solidity
(, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
if (block.timestamp - updatedAt > maxStaleness[asset]) revert StalePrice();
```

The heartbeat should be set per feed at registration time (matching the pattern recommended in the referenced report's fix), since different Chainlink feeds have different update frequencies (e.g., ETH/USD updates every 20 minutes on Optimism, while some feeds update only every 24 hours).

---

### Proof of Concept

1. Protocol supports stETH with a Chainlink stETH/ETH feed whose heartbeat is 1 hour.
2. The Chainlink feed stops updating (e.e., sequencer downtime). Last recorded price: 0.9999 ETH per stETH.
3. Real market price drops to 0.95 ETH per stETH (e.g., depeg event).
4. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
5. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns 0.9999 — no revert, no staleness check.
6. `rsETHPrice` is set ~5% above its true value.
7. Attacker redeems rsETH via `LRTWithdrawalManager`, receiving ~5% more ETH than their fair share.
8. Honest holders are left with a protocol that cannot cover their redemptions.

**Root cause line:** [1](#0-0) 

**Oracle consumed by LRTOracle here:** [2](#0-1) 

**rsETH price update that uses the stale price:** [3](#0-2) 

**Contrast — partial staleness check present in pool oracle (but absent in ChainlinkPriceOracle):** [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L214-250)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```
