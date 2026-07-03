### Title
Unsafe `int256`→`uint256` Cast in `ChainlinkPriceOracle.getAssetPrice` Without Sign Validation — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice` casts the raw `int256 price` returned by Chainlink directly to `uint256` without first verifying `price > 0`. A negative Chainlink answer silently wraps to a value near `type(uint256).max`, inflating every asset's ETH-denominated price used to compute the protocol-wide rsETH exchange rate. The same codebase already applies the correct guard in a sibling oracle contract, confirming the fix is known but inconsistently applied.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` fetches the latest Chainlink round and immediately casts the signed result to an unsigned integer:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

There is no `require(price > 0)` guard. If `price` is `-1`, then `uint256(-1)` evaluates to `2^256 - 1 ≈ 1.16 × 10^77`, making the asset appear worth an astronomical amount of ETH.

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral` explicitly guards against this:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / ...;
``` [2](#0-1) 

The corrupted price propagates through the following call chain:

1. `LRTOracle._getTotalEthInProtocol` calls `getAssetPrice(asset)` for every supported LST asset and accumulates `totalETHInProtocol`. [3](#0-2) 

2. `LRTOracle._updateRsETHPrice` divides `totalETHInProtocol` by `rsethSupply` to produce `newRsETHPrice`. [4](#0-3) 

3. `updateRSETHPrice` is a public, permissionless function — any caller can trigger the price update. [5](#0-4) 

---

### Impact Explanation

**Critical — Protocol insolvency / permanent freezing of funds.**

If `rsETHPrice` is set to an astronomically large value:

- All subsequent depositors receive effectively zero rsETH for any deposit amount (rsETH minted = `depositValue / rsETHPrice ≈ 0`), permanently locking their deposited LSTs in the pool with no corresponding rsETH issued.
- Existing rsETH holders who initiate withdrawals before the price is corrected can claim a disproportionate share of the underlying LST collateral, draining the protocol.
- The `pricePercentageLimit` guard in `_updateRsETHPrice` can block the update only if `pricePercentageLimit > 0` and the caller is not a manager; if the limit is unset (zero), the astronomical price is written unconditionally. [6](#0-5) 

---

### Likelihood Explanation

**Low.** Chainlink aggregators can legitimately return non-positive answers during circuit-breaker events, aggregator misconfiguration, or extreme market dislocations (historical precedent: LUNA collapse, stETH depeg). The trigger is an external condition, but the contract's responsibility is to validate the answer before using it — exactly as the sibling contract already does.

---

### Recommendation

Add an explicit sign check before the cast, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
require(price > 0, "ChainlinkPriceOracle: invalid price");
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Alternatively, use OpenZeppelin's `SafeCast.toUint256(price)`, which reverts on negative input, consistent with the remediation applied in the referenced external report.

---

### Proof of Concept

1. Chainlink's `latestRoundData` for a supported LST asset (e.g., stETH/ETH) returns `price = -1` (circuit breaker or misconfiguration).
2. `ChainlinkPriceOracle.getAssetPrice` computes `uint256(-1) * 1e18 / 1e18 = 2^256 - 1`.
3. Any caller invokes the public `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol` accumulates `(2^256 - 1) * totalAssetAmt`, overflowing or producing a near-maximal `totalETHInProtocol`.
5. `newRsETHPrice = totalETHInProtocol / rsethSupply` is set to an astronomical value and written to `rsETHPrice`.
6. All new depositors receive `≈ 0` rsETH; existing rsETH holders can redeem for the full collateral pool, draining it. [7](#0-6) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-34)
```text
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
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

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
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
