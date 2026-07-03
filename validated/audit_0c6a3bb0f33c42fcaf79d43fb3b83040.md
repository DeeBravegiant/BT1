### Title
Chainlink Price Not Validated for Zero/Negative Value in `getAssetPrice` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` fetches `int256 price` from Chainlink's `latestRoundData()` but never checks whether `price <= 0` before casting it to `uint256` and returning it. This is the direct analog of M-9: a price oracle whose return value is not adequately validated, allowing zero or negative prices to propagate into critical protocol calculations.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and immediately casts the result to `uint256` without any non-zero or non-negative guard:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Two failure modes exist:

1. **`price == 0`**: `uint256(0)` is returned. Every downstream consumer that multiplies by this price computes zero ETH value for the asset.
2. **`price < 0`** (e.g., `-1`): Solidity's unchecked cast produces `type(uint256).max`, massively inflating the asset's ETH value.

Contrast this with `ChainlinkOracleForRSETHPoolCollateral.sol` in the same repository, which correctly validates:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The missing check in `ChainlinkPriceOracle` is inconsistent with the protocol's own defensive pattern.

---

### Impact Explanation

`LRTOracle.getAssetPrice()` delegates directly to `ChainlinkPriceOracle.getAssetPrice()`: [3](#0-2) 

This price is consumed in three critical paths:

**1. rsETH price update (`_getTotalEthInProtocol`):**
Each supported asset's ETH value is computed as `totalAssetAmt.mulWad(assetER)`. A zero `assetER` silently zeroes out that asset's contribution to total protocol ETH, causing `newRsETHPrice` to be computed too low. [4](#0-3) 

**2. Deposit minting (`getRsETHAmountToMint`):**
`rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. If `getAssetPrice` returns 0, the depositor receives **0 rsETH** for their deposited assets — funds are effectively lost to the depositor. [5](#0-4) 

**3. Withdrawal payout (`_calculatePayoutAmount`):**
`currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice`. If `assetPrice == 0`, this is a **division by zero**, causing every withdrawal for that asset to revert — a temporary freeze of user funds. [6](#0-5) 

The negative-price scenario (huge `uint256`) inflates `totalETHInProtocol`, causing rsETH price to spike and triggering the `PriceAboveDailyThreshold` revert for non-manager callers, or minting excessive protocol fee rsETH to treasury — protocol insolvency. [7](#0-6) 

---

### Likelihood Explanation

Chainlink feeds can return `0` or negative values during circuit-breaker events, feed deprecation, or oracle malfunction. Chainlink's own documentation and integration guides recommend checking `answer > 0`. The protocol already applies this check in `ChainlinkOracleForRSETHPoolCollateral`, confirming awareness of the risk. The missing check in `ChainlinkPriceOracle` is a straightforward oversight that can be triggered by any Chainlink feed anomaly without any attacker action — a depositor or withdrawer simply calling the affected functions during such an event is sufficient.

---

### Recommendation

Add a non-positive price guard in `ChainlinkPriceOracle.getAssetPrice()`:

```diff
 (, int256 price,,,) = priceFeed.latestRoundData();
+if (price <= 0) revert InvalidPrice();
 return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [8](#0-7) 

---

### Proof of Concept

1. Chainlink's `latestRoundData()` for a supported LST asset (e.g., stETH/ETH) returns `price = 0` during a circuit-breaker event.
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0`.
3. `LRTOracle.getAssetPrice(stETH)` returns `0`.
4. A user calls `LRTDepositPool.depositAsset(stETH, 1e18, ...)`:
   - `getRsETHAmountToMint(stETH, 1e18)` computes `(1e18 * 0) / rsETHPrice = 0`.
   - User's 1e18 stETH is transferred in, but `0` rsETH is minted — depositor loses funds.
5. Alternatively, a user calls `LRTWithdrawalManager.unlockQueue(stETH, ...)`:
   - `_createUnlockParams` fetches `assetPrice = 0`.
   - `_calculatePayoutAmount` executes `(rsETHUnstaked * rsETHPrice) / 0` → division by zero → revert.
   - All pending stETH withdrawals are frozen until the oracle recovers. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-32)
```text
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-833)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
```

**File:** contracts/LRTWithdrawalManager.sol (L846-849)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
```
