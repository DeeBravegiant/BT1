### Title
Missing Staleness and Validity Checks on Chainlink `latestRoundData()` Return Values - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards all return values except `price`, performing no staleness check, no negative/zero price guard, and no round-completeness check. This stale or invalid price propagates directly into rsETH minting and withdrawal calculations.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The return tuple `(roundId, answer, startedAt, updatedAt, answeredInRound)` is fully destructured but only `answer` (`price`) is used. The following validations are absent:

- **Staleness**: `updatedAt` is never compared to `block.timestamp`. If a Chainlink feed stops updating (e.g., sequencer downtime, feed deprecation, or low liquidity), the last cached price is silently accepted.
- **Round completeness**: `answeredInRound >= roundId` is never checked. An in-progress round can return an incomplete answer.
- **Non-positive price**: `price` is cast directly to `uint256` without checking `price > 0`. A zero or negative `int256` answer would produce 0 or a wrapped-around enormous `uint256`.

This price is consumed by `LRTOracle.getAssetPrice()` → `LRTOracle._getTotalEthInProtocol()` → `LRTOracle._updateRsETHPrice()`, which computes the canonical `rsETHPrice` used for all minting and withdrawal accounting. [2](#0-1) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly validates all three conditions: [3](#0-2) 

### Impact Explanation
A stale or invalid asset price fed into `_getTotalEthInProtocol()` causes `rsETHPrice` to be computed incorrectly. Depositors calling `depositAsset()` or `depositETH()` receive an incorrect number of rsETH tokens relative to the true asset value. Existing rsETH holders are diluted (if price is understated) or new depositors are shortchanged (if price is overstated). The protocol fails to deliver the promised exchange rate.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
Chainlink feeds can go stale during network congestion, sequencer outages (on L2), or when a feed is deprecated. This is a known, documented failure mode. No attacker action is required — the condition arises from normal infrastructure failure. Any user calling `updateRSETHPrice()` (a public, permissionless function) during a stale-feed window triggers the mispricing. [4](#0-3) 

### Recommendation
Add the standard Chainlink validation pattern to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(price > 0, "Invalid price");
require(updatedAt != 0, "Incomplete round");
require(answeredInRound >= roundId, "Stale price");
require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Price too old");

return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

This mirrors the validation already present in `ChainlinkOracleForRSETHPoolCollateral`.

### Proof of Concept

1. A supported LST asset (e.g., stETH) has its Chainlink feed configured in `ChainlinkPriceOracle`.
2. The Chainlink feed stops updating (sequencer outage or feed deprecation). The last reported price is, say, 0.95 ETH, while the true price has moved to 1.02 ETH.
3. Any user calls `LRTOracle.updateRSETHPrice()` (public, no access control).
4. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale 0.95 ETH price with no revert.
5. `rsETHPrice` is computed using the understated TVL.
6. A depositor calling `depositAsset(stETH, amount, ...)` receives fewer rsETH tokens than the true exchange rate warrants, with no slippage protection against oracle staleness. [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-33)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

```

**File:** contracts/LRTDepositPool.sol (L99-118)
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
    }
```
