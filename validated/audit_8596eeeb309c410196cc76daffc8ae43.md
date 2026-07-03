All code references check out exactly as claimed. The vulnerability is confirmed valid.

Audit Report

## Title
Missing Staleness Check in `ChainlinkPriceOracle.getAssetPrice()` Enables Permissionless Protocol-Wide Pause - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` discards all `latestRoundData()` return values except `price`, performing no staleness, incomplete-round, or non-positive price validation. Because `LRTOracle.updateRSETHPrice()` is an unrestricted public function, any caller can invoke it while a Chainlink LST/ETH feed is stale, causing the computed `newRsETHPrice` to fall below `highestRsethPrice` by more than `pricePercentageLimit` and triggering the automatic downside-protection pause on `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself, freezing all user deposits and withdrawals until an admin manually unpauses each contract.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 captures only `price` from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

No check is made on `updatedAt` (heartbeat staleness), `answeredInRound < roundId` (incomplete round), or `price <= 0`. This contrasts directly with `ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 30–32), which guards all three conditions with explicit reverts.

The stale price propagates through:
1. `LRTOracle.getAssetPrice(asset)` → `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` (line 157)
2. `_getTotalEthInProtocol()` sums `totalAssetAmt.mulWad(assetER)` for every supported LST (line 343)
3. `_updateRsETHPrice()` computes `newRsETHPrice = (totalETHInProtocol - fee) / rsethSupply` (line 250)
4. Lines 270–281: if `newRsETHPrice < highestRsethPrice` and `diff > pricePercentageLimit.mulWad(highestRsethPrice)`, the function calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`, and returns early without updating `rsETHPrice`

The entry point `updateRSETHPrice()` (line 87) carries only the `whenNotPaused` modifier — no role restriction — making it callable by any EOA or contract.

## Impact Explanation
**Medium — Temporary freezing of funds.** When the pause triggers, all user deposits (`LRTDepositPool`) and withdrawals (`LRTWithdrawalManager`) are frozen. The freeze persists until an admin calls `unpause()` on each of the three contracts. Because the oracle itself is also paused, no price update can occur until admin intervention. This constitutes a temporary but complete freeze of user fund access, matching the allowed impact "Medium. Temporary freezing of funds."

## Likelihood Explanation
Chainlink LST/ETH feeds (e.g., stETH/ETH) operate on 24-hour heartbeats with 0.5% deviation thresholds. During periods of high gas prices, network congestion, or sequencer downtime on L2, updates can lag materially. If `pricePercentageLimit` is set to any non-zero value (e.g., 1e16 for 1%), a stale price that is even 1% below the true market price is sufficient to trigger the pause. The trigger is fully permissionless — any user, bot, or MEV searcher can call `updateRSETHPrice()` at the worst moment. The attack is repeatable: after each admin unpause, the same caller can re-trigger the pause as long as the feed remains stale.

## Recommendation
Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > HEARTBEAT_THRESHOLD) revert StalePrice();
```

Additionally, consider restricting `updateRSETHPrice()` to a keeper/operator role, or adding a circuit-breaker that distinguishes genuine price drops from oracle failures before triggering a protocol-wide pause.

## Proof of Concept
1. Deploy a mock Chainlink aggregator for a supported LST (e.g., stETH) that returns a price 1% below the current `highestRsethPrice`-implied value, with a stale `updatedAt` timestamp.
2. Set `pricePercentageLimit` to `1e16` (1%) via `setPricePercentageLimit`.
3. Call `LRTOracle.updateRSETHPrice()` from an unprivileged EOA.
4. Observe: `_getTotalEthInProtocol()` returns a value 1% lower than expected → `newRsETHPrice` is 1% below `highestRsethPrice` → `isPriceDecreaseOffLimit` is `true` → `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called.
5. Verify that subsequent deposit and withdrawal calls revert with paused errors.
6. Verify that only an admin calling `unpause()` on each contract restores functionality.

**Foundry fork test outline:**
```solidity
function testStaleOracleTriggersPause() public {
    // fork mainnet, set mock stale price 1% below current
    vm.mockCall(chainlinkFeed, abi.encodeWithSelector(latestRoundData.selector),
        abi.encode(roundId, stalePrice, startedAt, staleTimestamp, roundId - 1));
    vm.prank(attacker); // unprivileged
    lrtOracle.updateRSETHPrice();
    assertTrue(lrtDepositPool.paused());
    assertTrue(withdrawalManager.paused());
    assertTrue(lrtOracle.paused());
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
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
            }
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```
