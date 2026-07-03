Audit Report

## Title
No Staleness Check on Chainlink Price Feed Allows Stale Prices to Drive Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing zero staleness validation. The stale price flows directly into `LRTDepositPool.getRsETHAmountToMint()` as the live numerator against a stored `rsETHPrice` denominator, allowing a depositor to receive excess rsETH during a feed outage and diluting existing holders' proportional claim on TVL.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` silently discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
``` [1](#0-0) 

There is no `block.timestamp - updatedAt > threshold` check, no `answeredInRound < roundId` guard, and no per-asset heartbeat mapping. The returned `price` is accepted unconditionally regardless of age.

This stale price is consumed live in `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

`lrtOracle.getAssetPrice(asset)` is a live call that routes through `ChainlinkPriceOracle` and returns the stale price immediately. [3](#0-2) 

`lrtOracle.rsETHPrice()` is a **stored** value updated only when `updateRSETHPrice()` is called separately. [4](#0-3) 

This asymmetry is the crux of the exploit: if a Chainlink feed freezes at an inflated price after the last `rsETHPrice` update, the numerator is inflated while the denominator remains at the correct historical value, causing over-minting. The `minRSETHAmountExpected` slippage parameter set by the depositor provides no protection to existing holders — a depositor exploiting a stale inflated price would simply set a higher minimum to match the inflated output.

`_beforeDeposit()` calls `getRsETHAmountToMint()` and then mints directly with no additional price validation layer. [5](#0-4) 

A secondary instance exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which only checks `answeredInRound < roundID` (a deprecated Chainlink pattern that does not catch time-based staleness) and never validates `block.timestamp - timestamp` against any threshold. [6](#0-5) 

## Impact Explanation
**High — Theft of unclaimed yield.** When a Chainlink LST/ETH feed freezes at a price above the true market value, any depositor calling `depositAsset()` has their deposit over-valued. They receive excess rsETH tokens, which represent a larger proportional claim on the protocol's TVL than they paid for. Every existing rsETH holder's proportional claim is diluted by the excess minted supply. This constitutes theft of unclaimed yield from existing holders and maps directly to the allowed High impact class.

The deflated-price scenario (depositor receives fewer rsETH) maps to the Low impact class: "Contract fails to deliver promised returns."

## Likelihood Explanation
Any unprivileged user can trigger this by calling `depositAsset()` or `depositETH()` during a feed outage — no special role, no governance capture, and no victim mistake is required. Chainlink feed outages are documented real-world events (the ETH/USD feed experienced a 6-hour delay). The protocol supports multiple LST assets, each backed by its own Chainlink feed, multiplying the attack surface. Likelihood is **Medium**.

## Recommendation
1. Add a per-asset staleness threshold mapping to `ChainlinkPriceOracle`:
   ```solidity
   mapping(address asset => uint256 maxAge) public stalenessThreshold;
   ```
2. Enforce it in `getAssetPrice()`:
   ```solidity
   (, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
   if (block.timestamp - updatedAt > stalenessThreshold[asset]) revert StalePriceFeed();
   ```
3. Set each threshold slightly above the feed's documented heartbeat (e.g., 1 hour + buffer for ETH/USD, 24 hours + buffer for slower feeds).
4. Apply the same time-based fix to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, replacing the deprecated `answeredInRound < roundID` pattern with a `block.timestamp - timestamp > maxAge` check.

## Proof of Concept
**Foundry fork test outline:**

1. Fork mainnet at a block where a supported LST/ETH Chainlink feed is live.
2. Deploy or reference the existing `ChainlinkPriceOracle` and `LRTDepositPool`.
3. Record `rsETHPrice` (stored) and the current live `getAssetPrice(stETH)`.
4. Use `vm.mockCall` to make `latestRoundData()` return an inflated `price` with a stale `updatedAt` (e.g., `block.timestamp - 7 hours`).
5. Call `depositAsset(stETH, 1e18, 0, "")` as an unprivileged address.
6. Assert that `rsethAmountToMint` exceeds the amount that would have been minted at the true price, confirming over-minting with no revert.
7. Assert that existing holders' share of TVL per rsETH is diluted post-deposit.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
