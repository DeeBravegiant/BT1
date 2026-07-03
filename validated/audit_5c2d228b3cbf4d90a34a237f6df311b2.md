### Title
Missing Round Completeness and Staleness Checks in `ChainlinkPriceOracle.getAssetPrice()` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`. There is no check for round completeness (`answeredInRound >= roundId`), no staleness check on `updatedAt`, and no negative-price guard. Stale or incomplete Chainlink round data silently propagates into every deposit, withdrawal, and rsETH price update in the protocol.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the asset/ETH exchange rate from a Chainlink feed:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` is used. No validation is performed. [1](#0-0) 

By contrast, the sibling oracle wrapper `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

This inconsistency confirms the protocol is aware of the requirement but failed to apply it to `ChainlinkPriceOracle`.

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` flows into `LRTOracle.getAssetPrice()`: [3](#0-2) 

Which is consumed in three critical paths:

1. **Deposit minting** — `LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.getAssetPrice(asset)` to compute how many rsETH tokens to mint per deposited LST: [4](#0-3) 

2. **Withdrawal sizing** — `LRTWithdrawalManager.getExpectedAssetAmount()` uses `lrtOracle.getAssetPrice(asset)` to compute how many LST tokens a user receives when burning rsETH: [5](#0-4) 

3. **rsETH price update** — `LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice()` for every supported asset to compute total protocol TVL, which then sets `rsETHPrice` used globally: [6](#0-5) 

---

### Impact Explanation

**Scenario A — Stale inflated price (e.g., LST depegs after oracle round stalls):**
- `getAssetPrice(LST)` returns a price higher than the true market price.
- `getRsETHAmountToMint()` mints more rsETH than the deposited LST is worth.
- The depositor extracts excess rsETH backed by nothing, diluting all existing rsETH holders.
- This is protocol insolvency / theft of yield from existing holders.

**Scenario B — Stale deflated price:**
- `getExpectedAssetAmount()` returns fewer LST tokens than the user's rsETH is worth.
- Users receive less than the protocol promised — contract fails to deliver promised returns.

**Scenario C — Zero or negative price (incomplete round):**
- `uint256(price)` with a zero or negative `int256` causes arithmetic to produce 0 or wrap, making `rsETHAmountToMint` either 0 (DoS on deposits) or astronomically large (critical over-mint).

---

### Likelihood Explanation

Chainlink oracles can fail to start new rounds during network congestion, validator outages, or oracle node issues. The protocol supports multiple LST assets (stETH, ETHx, rETH, sfrxETH, swETH), each with its own Chainlink feed. Any one feed stalling is sufficient to trigger the vulnerability. The entry path (`depositAsset`, `initiateWithdrawal`, `instantWithdrawal`) is fully permissionless and callable by any user.

---

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "Invalid price");
    require(updatedAt != 0, "Incomplete round");
    require(answeredInRound >= roundId, "Stale price");
    require(block.timestamp - updatedAt <= HEARTBEAT, "Price too old");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

---

### Proof of Concept

1. Chainlink nodes for the stETH/ETH feed fail to reach consensus; no new round is started. `latestRoundData()` continues returning the last completed round's answer with an old `updatedAt` and `answeredInRound < roundId`.
2. The stETH price at the time of the stall was 1.05 ETH. The true current price has dropped to 0.95 ETH (depeg event).
3. An attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18` instead of `0.95e18`.
5. The attacker receives `1000e18 * 1.05e18 / rsETHPrice` rsETH — approximately 10.5% more than they should.
6. The attacker redeems the excess rsETH via `initiateWithdrawal`, extracting value from the protocol at the expense of existing rsETH holders.

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
