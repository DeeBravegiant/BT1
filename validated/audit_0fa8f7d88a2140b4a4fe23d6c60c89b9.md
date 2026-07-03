### Title
Stale Chainlink Price in `ChainlinkPriceOracle` Can Trigger Automatic Protocol Pause During Volatility, Freezing Deposits and Withdrawals - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`), and does not check that `price > 0`. During high market volatility — exactly when Chainlink feeds are most likely to be stale — any caller can invoke the public `LRTOracle.updateRSETHPrice()`, which uses the stale price to compute a falsely depressed rsETH price. If the computed drop exceeds `pricePercentageLimit`, the oracle's downside-protection logic automatically pauses `LRTDepositPool` and `LRTWithdrawalManager`, freezing all user deposits and withdrawals until an admin manually unpauses.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the LST/ETH exchange rate from a Chainlink aggregator:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The return values `updatedAt`, `answeredInRound`, and `roundId` are all silently discarded. There is no check that:
- `updatedAt` is recent (staleness guard)
- `answeredInRound >= roundId` (incomplete round guard)
- `price > 0` (invalid price guard)

By contrast, the protocol's own `ChainlinkOracleForRSETHPoolCollateral` — used for pool collateral — performs all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol:30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price flows into `LRTOracle._getTotalEthInProtocol()`, which multiplies each asset's balance by its oracle price to compute total ETH in the protocol:

```solidity
// contracts/LRTOracle.sol:339-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

`_updateRsETHPrice()` then computes `newRsETHPrice` from this total and checks it against `highestRsethPrice`:

```solidity
// contracts/LRTOracle.sol:270-281
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
```

`updateRSETHPrice()` is a public, permissionless function:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Any external caller can invoke it at any time.

---

### Impact Explanation

**Temporary freezing of funds (Medium).**

When a stale Chainlink price for any supported LST asset is consumed, `totalETHInProtocol` is artificially reduced. If the resulting `newRsETHPrice` falls more than `pricePercentageLimit` below `highestRsethPrice`, the oracle automatically pauses `LRTDepositPool` and `LRTWithdrawalManager`. All user deposits and withdrawals are frozen until an admin calls `unpause()` on each contract. During high volatility — the scenario most likely to produce stale Chainlink data — this freeze is most harmful, as users cannot exit positions or deposit to hedge.

---

### Likelihood Explanation

**Medium.**

Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/stETH). During network congestion or extreme volatility, updates can be delayed well beyond the heartbeat. The `updateRSETHPrice()` function is public and callable by anyone, including a user who notices the stale feed and calls it opportunistically (or a keeper bot that fires on schedule). The `pricePercentageLimit` is a configurable threshold; if set to a tight value (e.g., 1%), even a modest staleness-induced price discrepancy triggers the pause.

---

### Recommendation

Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be set per-feed based on its documented heartbeat interval.

---

### Proof of Concept

1. Chainlink's LST/ETH feed (e.g., stETH/ETH) has not updated for 2 hours due to network congestion during a volatile market event. Its `updatedAt` is stale but `latestRoundData()` still returns the old, lower price without reverting.

2. An external caller (any address) calls `LRTOracle.updateRSETHPrice()`. [1](#0-0) 

3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which calls `getAssetPrice(stETH)`. [2](#0-1) 

4. `ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and returns the stale price without any staleness validation. [3](#0-2) 

5. The stale price reduces `totalETHInProtocol`, causing `newRsETHPrice` to appear lower than `highestRsethPrice` by more than `pricePercentageLimit`.

6. The downside-protection branch fires, pausing `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`. [4](#0-3) 

7. All user deposits (`depositETH`, `depositAsset`) and withdrawals revert with `Pausable: paused` until an admin manually unpauses each contract. [5](#0-4)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L331-343)
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
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```
