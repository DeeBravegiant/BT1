Based on my analysis of the repository, I found a valid analog. The key oracle files are:

- `contracts/oracles/ChainlinkPriceOracle.sol` — L1 collateral price oracle, **no staleness check at all**
- `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` — L2 pool collateral oracle, only `answeredInRound < roundID` check, **no time-based staleness check**
- `contracts/cross-chain/CrossChainRateReceiver.sol` — L2 rsETH rate oracle, stores `lastUpdated` but `getRate()` **never validates it**

The most impactful root cause is in `ChainlinkPriceOracle.sol`: [1](#0-0) 

`getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound` entirely — no staleness guard of any kind.

---

### Title
No Chainlink Price Feed Staleness Check Allows Stale Collateral Prices to Drive rsETH Valuation — (`contracts/oracles/ChainlinkPriceOracle.sol`)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` fetches Chainlink `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` fields, performing zero staleness validation. Any collateral asset's Chainlink feed that goes stale (network congestion, oracle downtime, sequencer issues on L2) will silently return an outdated price that is then used to compute the rsETH/ETH exchange rate.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` is the price source for all supported collateral assets (stETH, ETHx, rETH, sfrxETH):

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt and answeredInRound silently dropped

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

This function is consumed by `LRTOracle._updateRsETHPrice()` (via `_getTotalEthInProtocol()`) to compute the total ETH value of all collateral held in the protocol, which then determines the rsETH/ETH price: [2](#0-1) [3](#0-2) 

`updateRSETHPrice()` is a public, permissionless function — any external caller can trigger it.

A secondary instance exists in `ChainlinkOracleForRSETHPoolCollateral.sol`, which is used by L2 pools (`RSETHPoolV3`, `RSETHPoolNoWrapper`) to price collateral tokens. It only checks `answeredInRound < roundID` (a deprecated Chainlink pattern that is unreliable on many feeds) and has no time-based staleness guard:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L26-37
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();   // no time-based check
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    ...
}
``` [4](#0-3) 

### Impact Explanation
**Critical / High.** If a collateral asset's Chainlink feed goes stale with an inflated price (e.g., stETH/ETH feed freezes during a depeg event), `_updateRsETHPrice()` will compute a falsely elevated `totalETHInProtocol`, minting an inflated rsETH price. This allows:

1. **Protocol insolvency**: rsETH is overvalued relative to actual backing; users redeeming at the inflated price drain real assets.
2. **Fee theft**: The protocol mints rsETH as fees based on `totalETHInProtocol - previousTVL`; a stale inflated price causes excess fee rsETH to be minted to the treasury.

For the L2 pool path (`ChainlinkOracleForRSETHPoolCollateral`), a stale collateral token price causes depositors to receive incorrect rsETH amounts — either over-minting (theft from the pool) or under-minting (loss to depositors).

### Likelihood Explanation
**Medium.** Chainlink feeds do go stale during network congestion, sequencer downtime (on L2s), or oracle operator issues. The protocol is explicitly deployed on multiple L2 chains (Arbitrum, Optimism, Base, Scroll, Unichain, Sonic) where sequencer outages are a known risk. The `updateRSETHPrice()` function is public and can be called by anyone at any time, including during a period when a feed is stale.

### Recommendation
1. In `ChainlinkPriceOracle.getAssetPrice()`, validate both `updatedAt` and `answeredInRound`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (block.timestamp - updatedAt > TIMEOUT) revert StalePrice();
if (price <= 0) revert InvalidPrice();
```

2. Use a per-asset `timeout` mapping rather than a single constant, since different Chainlink feeds have different heartbeats (e.g., stETH/ETH ~24h on mainnet, ETH/USD ~1h).

3. Apply the same fix to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, adding a time-based staleness check alongside the existing round check.

### Proof of Concept
1. Chainlink's stETH/ETH feed on Ethereum stops updating (e.g., last price was 0.9998 ETH, actual market price drops to 0.95 ETH during a depeg).
2. Anyone calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale 0.9998 price instead of 0.95.
4. `totalETHInProtocol` is computed ~5% higher than actual.
5. `newRsETHPrice` is set ~5% above real backing value.
6. Users can now redeem rsETH at the inflated price, withdrawing more stETH than the protocol can cover, leading to insolvency for remaining holders. [1](#0-0) [5](#0-4) [4](#0-3)

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

**File:** contracts/LRTOracle.sol (L214-231)
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
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
