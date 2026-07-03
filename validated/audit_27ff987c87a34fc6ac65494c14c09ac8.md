### Title
Chainlink Price Feed Staleness Not Validated in `ChainlinkPriceOracle`, Enabling Stale-Price-Based rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation fields (`updatedAt`, `answeredInRound`, `roundId`) and does not check that the returned `price` is positive. The same repository contains `ChainlinkOracleForRSETHPoolCollateral.sol`, which performs all three checks. The missing checks mean a stale or invalid Chainlink answer is silently accepted and propagated into rsETH minting and rsETH price updates, reachable by any unprivileged depositor.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink round data but ignores every field except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol  lines 49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Three fields are silently discarded:
- `updatedAt` — no heartbeat / staleness check
- `answeredInRound` vs `roundId` — no incomplete-round check
- sign of `price` — no `price > 0` guard; a negative `int256` cast to `uint256` produces a near-`type(uint256).max` value

By contrast, the sibling oracle in the same repository performs all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  lines 27-36
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle` is registered as the price oracle for supported assets (e.g., stETH, ETHx) in `LRTOracle.assetPriceOracle`. It is consumed in two critical paths:

1. **`LRTOracle._getTotalEthInProtocol()`** — iterates every supported asset, calls `getAssetPrice(asset)`, and sums the ETH-denominated TVL. This value drives `_updateRsETHPrice()`, which sets the global `rsETHPrice` used for all subsequent minting.
2. **`LRTDepositPool.getRsETHAmountToMint(asset, amount)`** — calls `lrtOracle.getAssetPrice(asset)` directly to compute how many rsETH tokens to mint for a depositor.

### Impact Explanation

**Stale price scenario (realistic):** If a Chainlink feed pauses or lags (e.g., during extreme market volatility or sequencer downtime on an L2), `updatedAt` falls behind the heartbeat threshold. The contract continues returning the last known price. If the stale price is *higher* than the true market price, every depositor calling `depositAsset()` receives more rsETH than their assets are worth, diluting all existing rsETH holders — a continuous theft of yield until the feed recovers or the oracle is replaced. If the stale price is *lower*, depositors receive fewer rsETH than owed, causing the contract to fail to deliver promised returns.

**Invalid/negative price scenario (edge case):** A Chainlink feed returning `price ≤ 0` (possible during feed misconfiguration or a sequencer incident) is cast to `uint256`, producing an astronomically large asset price. `_getTotalEthInProtocol()` overflows or returns a near-max value, causing `_updateRsETHPrice()` to set `rsETHPrice` to near-zero (division by a huge numerator), after which any depositor can mint an unbounded quantity of rsETH for a trivial ETH deposit — direct fund theft from the protocol.

**Impact classification:** High — theft of unclaimed yield (stale price above actual); escalates to Critical (direct fund theft) in the negative-price edge case.

### Likelihood Explanation

Chainlink feeds have documented historical incidents of staleness (e.g., during the March 2023 USDC depeg, feeds lagged significantly). The LRT-rsETH protocol supports multiple LST assets (stETH, ETHx, rETH, sfrxETH, swETH), each with its own feed; the probability that at least one feed experiences a staleness event over the protocol's lifetime is non-trivial. No heartbeat duration is enforced, so even a feed that is technically "live" but hours old is accepted. The entry path requires no privilege — any user calling `depositAsset()` triggers the vulnerable code.

### Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.sol`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: enforce a per-feed heartbeat
    // if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

### Proof of Concept

1. Assume stETH/ETH Chainlink feed last updated 4 hours ago (feed heartbeat is 1 hour); `updatedAt` is stale but `latestRoundData()` does not revert.
2. The stale price reflects stETH at 1.05 ETH; true market price is 1.00 ETH (feed froze during a depeg event).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, ...)`.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.05e18` (stale).
5. `rsethAmountToMint = (1000e18 * 1.05e18) / rsETHPrice` — attacker receives ~5% more rsETH than their deposit is worth.
6. Attacker repeats until the feed recovers, extracting yield from all existing rsETH holders with each deposit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
