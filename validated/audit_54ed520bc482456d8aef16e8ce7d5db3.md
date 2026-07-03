### Title
`ChainlinkPriceOracle.getAssetPrice` Performs No Staleness Validation on Chainlink Price Data - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary

`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but discards every staleness-related return value. There is no check on `updatedAt`, `roundId`, or `answeredInRound`. A stale Chainlink price is silently accepted and used to compute rsETH minting amounts for depositors, directly diluting or shortchanging existing rsETH holders.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` at line 52 calls `latestRoundData()` and captures only the `price` field, ignoring all other return values:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
``` [1](#0-0) 

There is no check of the form `block.timestamp - updatedAt <= heartbeat`, no `require(answer > 0)`, and no `answeredInRound >= roundId` guard (even the deprecated one). The function returns the raw price unconditionally.

`ChainlinkOracleForRSETHPoolCollateral.getRate` does attempt two checks, but both are invalid — mirroring the BendDAO pattern exactly:

```solidity
if (answeredInRound < roundID) revert StalePrice();   // deprecated, unreliable
if (timestamp == 0) revert IncompleteRound();          // not a heartbeat check
``` [2](#0-1) 

The primary attack surface is `ChainlinkPriceOracle`, which is the oracle backing `LRTOracle.getAssetPrice`. This price is consumed in two critical paths:

1. **Deposit minting** — `LRTDepositPool.getRsETHAmountToMint` divides `amount * lrtOracle.getAssetPrice(asset)` by `lrtOracle.rsETHPrice()` to determine how many rsETH tokens to mint for a depositor. [3](#0-2) 

2. **rsETH price update** — `LRTOracle._getTotalEthInProtocol` iterates all supported assets and multiplies each asset balance by `getAssetPrice(asset)` to compute total protocol ETH, which then sets `rsETHPrice`. [4](#0-3) 

### Impact Explanation

If a Chainlink feed goes stale (e.g., during network congestion, a sequencer outage, or a feed deprecation event) and the last reported price is **inflated** relative to the true market price:

- `getRsETHAmountToMint` returns an inflated rsETH amount for the depositor.
- The depositor receives more rsETH than their deposit is worth.
- This over-minting dilutes all existing rsETH holders, constituting theft of their proportional share of protocol TVL (theft of unclaimed yield / protocol insolvency in extreme cases).

If the stale price is **deflated**, depositors are shortchanged — the contract fails to deliver promised returns.

**Severity: High** — theft of unclaimed yield from existing rsETH holders when a stale inflated price is consumed.

### Likelihood Explanation

Chainlink feeds have documented heartbeat windows (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds). During periods of low volatility, feeds may not update for the full heartbeat duration. Network congestion or sequencer downtime can extend this further. Any depositor transacting during a stale window triggers the issue without any special privileges.

### Recommendation

Add a per-asset heartbeat staleness check in `ChainlinkPriceOracle.getAssetPrice`:

```solidity
(uint80 roundId, int256 price, , uint256 updatedAt, ) = priceFeed.latestRoundData();
require(price > 0, "Invalid price");
require(block.timestamp - updatedAt <= assetHeartbeat[asset], "Stale price");
```

Store a `mapping(address asset => uint256 heartbeat) public assetHeartbeat` and require it to be set alongside `updatePriceFeedFor`. Remove the deprecated `answeredInRound >= roundId` pattern from `ChainlinkOracleForRSETHPoolCollateral` and replace `timestamp == 0` with a proper heartbeat check as well.

### Proof of Concept

1. Chainlink's stETH/ETH feed last updated 2 hours ago at price `1.05e18` (true current price: `1.00e18`).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint` computes: `100e18 * 1.05e18 / rsETHPrice` → attacker receives ~5% more rsETH than fair value.
4. Attacker immediately redeems or holds, extracting value from existing rsETH holders.
5. No admin action, no special role, no front-running required — any depositor during the stale window benefits. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-31)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
```

**File:** contracts/LRTDepositPool.sol (L99-117)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
