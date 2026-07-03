Audit Report

## Title
Unchecked `int256`→`uint256` Cast in `ChainlinkPriceOracle.getAssetPrice` Enables Unbacked rsETH Minting — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` casts Chainlink's `int256 answer` directly to `uint256` with no guard on non-positive values. If the feed returns `price ≤ 0`, the cast produces a value near `type(uint256).max`. Because `LRTDepositPool.getRsETHAmountToMint` reads this live price at deposit time, an attacker can deposit a minimal LST amount and receive an astronomically large rsETH mint, breaking the protocol's backing invariant.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice` at lines 52–54 performs an unchecked cast:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

No `price <= 0` guard exists. The sister contract `ChainlinkOracleForRSETHPoolCollateral` in the same codebase explicitly guards against this at line 32:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The deposit path reads the live oracle price at call time via `getRsETHAmountToMint`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`lrtOracle.getAssetPrice(asset)` delegates directly to `ChainlinkPriceOracle.getAssetPrice` with no intermediate validation: [4](#0-3) 

**Exploit flow:**
1. Chainlink feed returns `price = -1` (or any negative value).
2. `uint256(-1) = type(uint256).max ≈ 1.157e77`.
3. With `minAmountToDeposit = 0` (default), attacker deposits `amount = 1 wei`. Solidity 0.8 checked arithmetic: `1 * type(uint256).max = type(uint256).max` — no overflow.
4. `rsethAmountToMint = type(uint256).max / rsETHPrice` where `rsETHPrice ≈ 1e18` → `≈ 1.157e59` rsETH minted for 1 wei of collateral.
5. Attacker sells or redeems this rsETH, draining real collateral from the protocol.

**Why existing checks fail:**
- The `pricePercentageLimit` guard in `_updateRsETHPrice` only gates the `updateRSETHPrice` call — a separate transaction. The deposit path reads the live oracle price independently and is not protected. [5](#0-4) 

- `updatePriceOracleForValidated` performs a sanity check (`price > 1e19 || price < 1e16`) only at oracle registration time, not at deposit time. Additionally, `updatePriceOracleFor` (without validation) is also callable by admin. [6](#0-5) 

## Impact Explanation

**Critical — Protocol insolvency.** An attacker mints rsETH unbacked by real collateral. The attacker can immediately sell or redeem this rsETH, draining the protocol's real collateral. All existing rsETH holders are diluted to near-zero backing. This matches the "Protocol insolvency" critical impact class.

## Likelihood Explanation

**Medium precondition likelihood; certain exploit once triggered.** Chainlink `latestRoundData` returns `int256`, and non-positive values can occur during feed misconfiguration, aggregator bugs, or circuit-breaker events. The exploit requires no privileged role, no front-running, and no special setup — a single `depositAsset` call suffices when the feed returns `price ≤ 0`. The inconsistency with `ChainlinkOracleForRSETHPoolCollateral` confirms the omission is a defect, not a design choice.

## Recommendation

Add a positive-price guard in `ChainlinkPriceOracle.getAssetPrice`, matching the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Also add staleness checks (`updatedAt`, `answeredInRound < roundId`) consistent with `ChainlinkOracleForRSETHPoolCollateral`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;
import "forge-std/Test.sol";

contract MockNegativeFeed {
    function decimals() external pure returns (uint8) { return 18; }
    function latestRoundData() external pure returns (
        uint80, int256, uint256, uint256, uint80
    ) {
        return (1, -1, block.timestamp, block.timestamp, 1);
    }
}

contract ExploitTest is Test {
    function testNegativePriceExploit() public {
        MockNegativeFeed feed = new MockNegativeFeed();
        (, int256 price,,,) = feed.latestRoundData();
        assertLt(price, 0);

        // Unchecked cast: uint256(-1) = type(uint256).max
        uint256 wrapped = uint256(price);
        assertEq(wrapped, type(uint256).max);

        // amount=1 wei: 1 * type(uint256).max does NOT overflow (no overflow at multiplier=1)
        // rsethAmountToMint = type(uint256).max / 1e18 ≈ 1.157e59
        uint256 rsethAmountToMint = (1 * wrapped) / 1e18;
        assertGt(rsethAmountToMint, 1e50); // passes: attacker receives ~1.157e59 rsETH for 1 wei
    }
}
```

For a full fork test: deploy `LRTConfig`, `LRTOracle`, `ChainlinkPriceOracle`, `LRTDepositPool`, and `RSETH`; register `MockNegativeFeed` as the price feed for stETH; seed the protocol with honest deposits so `rsETHPrice ≈ 1e18`; call `depositAsset(stETH, 1, 0, "")` as attacker; assert `rsETH.balanceOf(attacker) >> actual stETH collateral`, confirming the backing invariant is broken.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-34)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L101-108)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
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
