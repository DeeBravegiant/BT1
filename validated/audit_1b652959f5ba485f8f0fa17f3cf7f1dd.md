Audit Report

## Title
Missing Non-Positive Price Validation in `getAssetPrice` Causes Deposit DoS - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` casts the `int256` Chainlink price directly to `uint256` without checking that it is positive. If any supported asset's feed returns a non-positive value, the unchecked cast followed by multiplication overflows under Solidity 0.8 checked arithmetic, reverting all deposit calls until the feed recovers. The sister contract `ChainlinkOracleForRSETHPoolCollateral` already applies the correct guard, confirming the fix is known.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` lines 52–54, `latestRoundData()` is called and the result is cast and multiplied without any sign check:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

If `price` is negative (e.g. `-1`), `uint256(-1)` equals `2^256 - 1`. The subsequent `* 1e18` overflows and reverts under Solidity 0.8's checked arithmetic. If `price` is `0`, the function returns `0`, which propagates silently incorrect accounting.

The call chain from a public deposit entry point is:

`LRTDepositPool.depositAsset()` / `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `ILRTOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)`. [2](#0-1) [3](#0-2) 

Additionally, the public `updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` path also calls `getAssetPrice` for every supported asset, so price updates would also revert. [4](#0-3) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` explicitly guards against this:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
``` [5](#0-4) 

No equivalent guard exists in `ChainlinkPriceOracle`.

## Impact Explanation
When any supported LST asset's Chainlink feed returns a non-positive price, every call to `depositAsset()` and `depositETH()` reverts. No new deposits can be accepted until the feed recovers. This constitutes a **temporary freezing of funds** (deposit functionality) matching the allowed Medium impact category. Existing depositor funds are not at risk of theft, but the protocol is unable to accept new capital during the outage.

## Likelihood Explanation
Chainlink aggregators can return `0` or a negative sentinel value during circuit-breaker events when the underlying asset price moves outside the feed's configured min/max bounds. This requires no attacker action — it is an organic market-stress condition. Any unprivileged user attempting to deposit during such an event will trigger the revert. The condition is temporary but can recur.

## Recommendation
Add a non-positive price guard before the cast, mirroring the pattern in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, add staleness checks (`updatedAt`, `answeredInRound`) as `ChainlinkOracleForRSETHPoolCollateral` does at lines 30–31. [6](#0-5) 

## Proof of Concept
1. Deploy or fork with a mock Chainlink feed for a supported LST (e.g. stETH) configured to return `price = -1` from `latestRoundData()`.
2. Call `LRTDepositPool.depositAsset(stETH, amount, 0, "")` from any EOA.
3. Execution reaches `ChainlinkPriceOracle.getAssetPrice(stETH)`.
4. `uint256(-1) * 1e18` overflows → Solidity 0.8 reverts with arithmetic overflow.
5. All deposit calls revert until the feed returns a positive value.

Foundry test sketch:
```solidity
function test_negativePrice_depositsRevert() public {
    mockFeed.setPrice(-1);
    vm.expectRevert(); // arithmetic overflow
    depositPool.depositAsset(stETH, 1 ether, 0, "");
}
```

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
