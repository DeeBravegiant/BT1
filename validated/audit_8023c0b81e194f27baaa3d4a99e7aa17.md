### Title
Unvalidated Chainlink Price in `ChainlinkPriceOracle.getAssetPrice()` Causes Incorrect rsETH Price Calculation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but performs no validation on the returned price (no staleness check, no zero/negative check, no round-completeness check). The result is consumed uncritically by `LRTOracle._getTotalEthInProtocol()`, which feeds directly into `_updateRsETHPrice()`. Because `updateRSETHPrice()` is a public function, any caller can trigger a price update during a period of stale or invalid Chainlink data, causing the stored `rsETHPrice` to be set incorrectly and enabling over-minting of rsETH at the expense of existing holders.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price with no guards:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Missing checks:
- **Staleness**: `updatedAt` is never compared to `block.timestamp`.
- **Round completeness**: `answeredInRound >= roundId` is never verified.
- **Zero/negative price**: `price <= 0` is never rejected.

By contrast, the sister contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` explicitly enforces all three:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unvalidated price flows into `LRTOracle._getTotalEthInProtocol()`:

```solidity
uint256 assetER = getAssetPrice(asset);          // no validation
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

`_getTotalEthInProtocol()` is called by `_updateRsETHPrice()`, which computes and stores the canonical `rsETHPrice`:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [4](#0-3) 

`rsETHPrice` is then used by `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens a depositor receives:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [5](#0-4) 

### Impact Explanation
If a Chainlink feed for any supported LST collateral returns a stale or zero price, `_getTotalEthInProtocol()` underestimates the protocol's TVL. The resulting `rsETHPrice` is set lower than the true value. Because `rsethAmountToMint` is inversely proportional to `rsETHPrice`, a depositor who acts immediately after the corrupted price update receives more rsETH than their deposit is worth. This dilutes all existing rsETH holders — a direct theft of value from at-rest funds.

**Impact: High — Theft of unclaimed yield / value from existing rsETH holders.**

### Likelihood Explanation
Chainlink feeds do go stale during network congestion, oracle downtime, or circuit-breaker events. `updateRSETHPrice()` is a public, permissionless function callable by anyone:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [6](#0-5) 

An attacker can monitor Chainlink feeds off-chain, wait for a feed to go stale or return an anomalous value, then immediately call `updateRSETHPrice()` to lock in the bad price, followed by a deposit to capture the inflated rsETH mint. No privileged access is required.

### Recommendation
Add staleness, round-completeness, and non-zero/non-negative checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

Additionally, consider adding a zero-price guard inside `_getTotalEthInProtocol()` so that a single bad oracle cannot silently corrupt the global TVL calculation.

### Proof of Concept
1. A Chainlink LST/ETH feed (e.g., stETH/ETH) goes stale — `updatedAt` is hours old and `answeredInRound < roundId`.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale (or zero) price without reverting.
4. `totalETHInProtocol` is underestimated; `newRsETHPrice` is set below true value and stored.
5. Attacker calls `LRTDepositPool.depositAsset(wstETH, largeAmount)`.
6. `getRsETHAmountToMint` divides by the artificially low `rsETHPrice`, minting excess rsETH to the attacker.
7. When the oracle recovers and `rsETHPrice` is corrected upward, the attacker's rsETH is worth more than deposited — existing holders are diluted. [1](#0-0) [7](#0-6) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
