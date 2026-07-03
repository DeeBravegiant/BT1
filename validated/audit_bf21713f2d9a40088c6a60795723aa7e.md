### Title
No L2 Sequencer Uptime Check in Chainlink Oracle Allows Stale Price Usage on Arbitrum - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` with no check for whether the Arbitrum L2 sequencer is active. When the sequencer is down, Chainlink feeds continue to return the last known (stale) price. The protocol is explicitly deployed on Arbitrum, and this oracle is the critical price source used to compute rsETH minting amounts during user deposits.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the asset/ETH exchange rate from a Chainlink feed and returns it directly to `LRTOracle.getAssetPrice()`, which is then consumed by `LRTDepositPool.getRsETHAmountToMint()` during every deposit:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Three deficiencies are present simultaneously:
1. **No L2 sequencer uptime check** — there is no call to a Chainlink sequencer uptime feed to verify the Arbitrum sequencer is live.
2. **No staleness check** — the `updatedAt` return value is completely ignored; there is no `block.timestamp - updatedAt <= maxStaleness` guard.
3. **No price validity check** — `price` is cast directly to `uint256` without verifying `price > 0`, meaning a zero or negative answer would silently produce a zero or wrap-around value.

A grep across all production contracts confirms there is no sequencer uptime feed reference anywhere:

```
grep "sequencer|L2_SEQUENCER|uptime" contracts/**/*.sol → No matches found.
```

The call chain from a public entry point to the vulnerable oracle is:

```
LRTDepositPool.depositAsset() / depositETH()
  → _beforeDeposit()
    → getRsETHAmountToMint()
      → LRTOracle.getAssetPrice(asset)          // LRTOracle.sol L156-158
        → IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)
          → ChainlinkPriceOracle.getAssetPrice() // ← stale price returned here
```

`LRTOracle.rsETHPrice()` (the denominator in the mint calculation) is a stored value updated by a separate keeper call (`updateRSETHPrice`), so it does not self-correct during a sequencer outage. The numerator (asset price from Chainlink) is fetched live and will be stale.

A secondary instance exists in `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` (`getRate()`, line 26–37), which also calls `latestRoundData()` without a sequencer check. It does perform `answeredInRound < roundID` and `timestamp == 0` checks, but these do not protect against sequencer downtime — Chainlink continues to report the last round as valid while the sequencer is offline.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds / oracle/rate abuse enabling unfair minting.**

When the Arbitrum sequencer goes down:
- Chainlink feeds freeze at the last reported price.
- A malicious actor who observes that the true market price has moved significantly (e.g., an LST depegs or appreciates) can deposit assets at the stale favorable rate, minting more rsETH than they are entitled to, or conversely, other users receive fewer rsETH than fair value.
- This constitutes theft of unclaimed yield / dilution of existing rsETH holders, as the rsETH price is computed from total ETH in protocol divided by total rsETH supply.

---

### Likelihood Explanation

Arbitrum is explicitly listed as a deployment target in the project README. Arbitrum sequencer outages have occurred historically (e.g., December 2021, June 2023). The attack requires only that the sequencer be down and that the market price has moved during the outage — both are realistic conditions. No privileged access is required; any unprivileged depositor can exploit this.

---

### Recommendation

Follow the [Chainlink L2 sequencer uptime feed example](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code). Add a sequencer uptime check in `ChainlinkPriceOracle.getAssetPrice()` (and `ChainlinkOracleForRSETHPoolCollateral.getRate()`):

```solidity
// Example addition to ChainlinkPriceOracle
AggregatorV3Interface internal sequencerUptimeFeed;

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    // Check sequencer on L2
    (, int256 sequencerAnswer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (sequencerAnswer != 0) revert SequencerDown();
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();

    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
    require(price > 0, "Invalid price");
    require(block.timestamp - updatedAt <= MAX_STALENESS, "Stale price");
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Also add a staleness check using `updatedAt` and a `price > 0` guard, both of which are currently absent.

---

### Proof of Concept

1. Arbitrum sequencer goes offline. Chainlink oracle for stETH/ETH freezes at price `P_stale`.
2. True market price of stETH drops to `P_real < P_stale` (e.g., due to a depeg event).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")`.
4. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `P_stale` (stale, inflated).
5. Attacker receives `rsethAmountToMint = amount * P_stale / rsETHPrice` — more rsETH than the real value of their deposit warrants.
6. When sequencer comes back online and `updateRSETHPrice()` is called, the rsETH price drops, diluting all existing holders. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
