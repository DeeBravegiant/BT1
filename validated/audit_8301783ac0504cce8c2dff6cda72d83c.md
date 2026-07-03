### Title
Missing Chainlink Price Data Validity Checks Allow Stale or Invalid Prices to Be Accepted - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but discards all data-quality return values (`updatedAt`, `answeredInRound`, `roundId`). It also performs an unchecked `int256 → uint256` cast on `price`. This is the direct analog of the Pyth confidence-interval omission: a data-quality signal provided by the oracle is silently ignored, allowing stale or invalid prices to flow into every downstream calculation that determines how much rsETH a depositor receives.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` (line 52) reads the price feed as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Three data-quality fields are silently discarded:

| Field | What it signals | Missing check |
|---|---|---|
| `updatedAt` | Timestamp of last successful update | No heartbeat / staleness check |
| `answeredInRound` | Round in which the answer was computed | No `answeredInRound >= roundId` check |
| `price` (sign) | Negative value indicates circuit-breaker / invalid state | No `price > 0` guard before cast |

**Staleness path:** Chainlink feeds have a heartbeat (e.g., 1 h or 24 h). If the sequencer goes down (on L2) or the feed is temporarily paused, `updatedAt` can be hours or days old while `latestRoundData()` still returns the last cached value without reverting. The contract accepts it unconditionally.

**Negative-price path:** Chainlink's own documentation warns that `answer` can be negative (e.g., during circuit-breaker events). Casting a negative `int256` to `uint256` produces `2^256 − |price|`, an astronomically large number. `getAssetPrice` would then return this value to every caller. [1](#0-0) 

### Impact Explanation

`getAssetPrice` is consumed by:

1. **`LRTDepositPool.getRsETHAmountToMint`** — `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. If `getAssetPrice` returns an astronomically large value (negative-price cast), a depositor mints an enormous amount of rsETH for a tiny deposit, directly causing **protocol insolvency / fund theft**.

2. **`LRTDepositPool.getSwapETHToAssetReturnAmount`** — divides by `getAssetPrice`; a zero price (price == 0) causes a division-by-zero revert, **temporarily freezing** the swap path.

3. **`LRTWithdrawalManager._createUnlockParams`** — passes `lrtOracle.getAssetPrice(asset)` into `_calculatePayoutAmount`, which computes `(rsETHUnstaked * rsETHPrice) / assetPrice`. A stale or inflated asset price directly distorts withdrawal payouts. [2](#0-1) [3](#0-2) 

**Impact:** High — incorrect rsETH minting from a bad price leads to protocol insolvency; a zero price freezes the swap path.  
**Likelihood:** Low — requires Chainlink to return a stale, zero, or negative price, which is uncommon but documented as possible (circuit-breaker events, sequencer downtime on L2, feed deprecation).

### Likelihood Explanation

Chainlink's own best-practices documentation explicitly requires callers to validate `updatedAt` against a heartbeat threshold and to check that `answer > 0`. These conditions can be triggered by:
- Sequencer downtime on L2 deployments (stale cached answer returned)
- Feed circuit-breaker activation during extreme market events (negative or zero answer)
- Feed deprecation / migration (last round never updated again)

None of these require attacker action; they are external conditions that the contract must defend against, exactly as the Pyth confidence interval must be checked regardless of whether Pyth misbehaves.

### Recommendation

```diff
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

-   (, int256 price,,,) = priceFeed.latestRoundData();
+   (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
+       priceFeed.latestRoundData();
+   if (price <= 0) revert InvalidPrice();
+   if (updatedAt == 0 || block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
+   if (answeredInRound < roundId) revert IncompleteRound();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should match the feed's documented heartbeat (e.g., 3600 s for a 1-hour heartbeat feed).

### Proof of Concept

1. Chainlink's ETHx/ETH (or any supported LST/ETH) feed enters a circuit-breaker state and returns `price = -1`.
2. `ChainlinkPriceOracle.getAssetPrice(ethxAddress)` executes `uint256(-1)` = `2^256 - 1`, returning `~1.16 × 10^77`.
3. `LRTOracle.getAssetPrice(ethxAddress)` returns this value to `LRTDepositPool.getRsETHAmountToMint`.
4. A depositor calling `depositAsset(ethxAddress, 1 wei, ...)` receives `(1 * 2^256-1) / rsETHPrice` rsETH — effectively the entire rsETH supply and more.
5. Protocol is insolvent; all other depositors' funds are stolen. [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```
