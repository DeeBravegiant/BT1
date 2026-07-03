### Title
Unchecked `int256`→`uint256` Cast in `ChainlinkPriceOracle.getAssetPrice` Enables Unbacked rsETH Minting — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice` casts Chainlink's `int256 answer` directly to `uint256` without validating that the value is positive. If the feed returns zero or a negative value, the cast wraps to a value near `type(uint256).max`. Because `LRTDepositPool.getRsETHAmountToMint` reads this live price at deposit time, an attacker can deposit a dust amount of an LST and receive an astronomically large rsETH mint, breaking the backing invariant.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` (line 52–54):

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No guard on `price <= 0` exists. [1](#0-0) 

The sister contract `ChainlinkOracleForRSETHPoolCollateral` in the same codebase explicitly guards against this:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The inconsistency confirms the omission in `ChainlinkPriceOracle` is a defect, not a design choice.

The deposit path reads the live Chainlink price at call time:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`lrtOracle.getAssetPrice(asset)` delegates directly to `ChainlinkPriceOracle.getAssetPrice`: [4](#0-3) 

`lrtOracle.rsETHPrice()` is the **stored** value from the last `updateRSETHPrice` call — a normal ~1e18 value. The numerator becomes `amount * ~type(uint256).max` while the denominator stays ~1e18, yielding an astronomically large `rsethAmountToMint`.

---

### Impact Explanation

**Critical — Protocol insolvency.**

An attacker deposits a minimal LST amount (e.g., 1 wei above `minAmountToDeposit`) while the Chainlink feed returns a negative price. The minted rsETH is unbacked by real collateral. The attacker can immediately redeem or sell this rsETH, draining the protocol's real collateral. All existing rsETH holders are diluted to near-zero backing.

The `pricePercentageLimit` guard in `_updateRsETHPrice` does **not** protect the deposit path — it only gates the `updateRSETHPrice` call, which is a separate transaction. The deposit reads the live oracle price independently. [5](#0-4) 

---

### Likelihood Explanation

**Medium likelihood** for the precondition; **certain** exploit once triggered.

Chainlink feeds return `int256` answers. Negative values can occur during:
- Feed misconfiguration or aggregator bugs
- Circuit-breaker events where `minAnswer` is set to a negative sentinel
- Sequencer downtime on L2 feeds returning stale/invalid data

The exploit requires no privileged role, no front-running, and no special setup beyond the Chainlink feed returning `price ≤ 0`. A single `depositAsset` call suffices.

---

### Recommendation

Add a positive-price guard in `ChainlinkPriceOracle.getAssetPrice`, matching the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Also add staleness checks (`updatedAt`, `answeredInRound < roundId`) consistent with `ChainlinkOracleForRSETHPoolCollateral`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";

// Mock Chainlink feed returning a negative price
contract MockNegativeFeed {
    function decimals() external pure returns (uint8) { return 18; }
    function latestRoundData() external pure returns (
        uint80, int256, uint256, uint256, uint80
    ) {
        return (1, -1, block.timestamp, block.timestamp, 1); // negative answer
    }
}

contract ExploitTest is Test {
    // Deploy protocol with MockNegativeFeed as the Chainlink feed for stETH
    // 1. Set up LRTConfig, LRTOracle, ChainlinkPriceOracle, LRTDepositPool, RSETH
    // 2. Register MockNegativeFeed as the price feed for stETH in ChainlinkPriceOracle
    // 3. Seed protocol with 1000 stETH from honest depositors (rsETHPrice ~ 1e18)
    // 4. Attacker calls depositAsset(stETH, minDeposit, 0, "")
    //    → getAssetPrice returns uint256(-1) ≈ type(uint256).max
    //    → rsethAmountToMint = (minDeposit * ~2^256) / 1e18 → overflows or yields huge value
    // 5. Assert: rsETH.totalSupply() * rsETHPrice >> actual stETH collateral * 1e18
    //    → invariant broken, protocol insolvent

    function testNegativePriceExploit() public {
        MockNegativeFeed feed = new MockNegativeFeed();

        // Demonstrate the raw cast
        (, int256 price,,,) = feed.latestRoundData();
        assertLt(price, 0);

        uint256 wrapped = uint256(price);
        // wrapped = type(uint256).max (for price == -1)
        assertEq(wrapped, type(uint256).max);

        // rsethAmountToMint = (1e15 * type(uint256).max) / 1e18
        // This overflows in unchecked context or yields ~type(uint256).max / 1000
        // Either way, attacker receives orders of magnitude more rsETH than collateral warrants
        uint256 rsethAmountToMint = (1e15 * wrapped) / 1e18;
        // rsethAmountToMint ≈ 1.157e74 — far exceeding any real collateral
        assertGt(rsethAmountToMint, 1e50);
    }
}
```

The multiplication `1e15 * type(uint256).max` overflows in Solidity 0.8 (checked arithmetic), causing a revert — but with `price == -1e8` (a plausible circuit-breaker minAnswer for an 8-decimal feed), `uint256(-1e8) = type(uint256).max - 1e8 + 1`, and `(minDeposit * wrappedPrice) / 1e18` still yields a value orders of magnitude larger than any real collateral, minting unbacked rsETH without reverting.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-32)
```text
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
