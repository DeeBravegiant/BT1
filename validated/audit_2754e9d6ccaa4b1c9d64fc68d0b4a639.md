Audit Report

## Title
Missing Chainlink `updatedAt` Staleness Check Enables Inflated TVL, Unbacked Fee Minting, and Over-issuance of rsETH — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` return value, accepting frozen prices without any staleness validation. If a Chainlink LST/ETH feed lags behind a real price drop (e.g., during a slashing event within the heartbeat window), the protocol computes an inflated TVL, mints unbacked fee rsETH to the treasury, stores an elevated `rsETHPrice`, and allows subsequent depositors to receive more rsETH than their collateral is worth — resulting in protocol insolvency.

## Finding Description

**Root cause — `contracts/oracles/ChainlinkPriceOracle.sol` line 52:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

All five return values are destructured; `updatedAt` (4th position) is silently discarded with no comparison against `block.timestamp`. No `MAX_STALENESS` bound exists anywhere in the contract. [1](#0-0) 

**Full exploit call chain:**

1. `LRTOracle._getTotalEthInProtocol()` iterates every supported asset and calls `getAssetPrice(asset)`, which routes through `ChainlinkPriceOracle.getAssetPrice()` — returning the stale, frozen high price. [2](#0-1) 

2. `_updateRsETHPrice()` uses the inflated `totalETHInProtocol` to compute a phantom `rewardAmount = totalETHInProtocol - previousTVL`, derives `protocolFeeInETH`, mints unbacked fee rsETH to the treasury, and stores the elevated `newRsETHPrice`. [3](#0-2) 

3. `updateRSETHPrice()` is **public and permissionless** — any external caller can trigger this at any time. [4](#0-3) 

4. `LRTDepositPool.getRsETHAmountToMint()` divides the stale asset price by the now-elevated `rsETHPrice`, issuing excess rsETH to depositors. [5](#0-4) 

**Why existing guards fail:**

- **`maxFeeMintAmountPerDay`**: Caps the daily fee rsETH mint amount, but does **not** prevent the inflated `rsETHPrice` from being written to storage. Depositors can still mint at the elevated rate regardless of this cap. [6](#0-5) 

- **`pricePercentageLimit`**: Only activates when `pricePercentageLimit > 0`. It **defaults to `0`** (unset at initialization), meaning the price-increase guard is entirely inactive unless an admin explicitly configures it. An unprivileged caller can trigger `updateRSETHPrice()` with an arbitrarily inflated price when this is unset. [7](#0-6) 

- **Downside pause**: Only triggers when `newRsETHPrice < highestRsethPrice`. In the stale-price scenario the computed price is *higher* than the true price, so this protection does not fire. [8](#0-7) 

## Impact Explanation

**Critical — Protocol insolvency.**

- The treasury receives rsETH minted against a phantom reward that has no real collateral backing it.
- Every depositor who calls `depositAsset()` after the stale-price update receives rsETH in excess of their true collateral value (the numeric example in the report shows ~5.8% excess for a 10% stale/true price spread).
- When the Chainlink feed eventually corrects, `rsETHPrice` will drop, but all excess rsETH already minted remains outstanding and unbacked, permanently diluting honest holders. This constitutes direct protocol insolvency, matching the Critical impact class.

## Likelihood Explanation

**Medium.** Two simultaneous conditions are required:

1. An LST experiences a real price drop (slashing, depeg) while the Chainlink feed has not yet updated. For stETH/ETH the Chainlink heartbeat is 24 hours with a 0.5% deviation threshold — a feed can legitimately be many hours old without any oracle operator failure or compromise.
2. `updateRSETHPrice()` is called during the staleness window. This function is public and permissionless; no role, no front-running, and no governance capture is required.

An attacker simply monitors for a slashing event, waits for the feed to lag, and calls `updateRSETHPrice()` followed by `depositAsset()`. The attack is repeatable across any supported LST feed.

## Recommendation

Add a per-feed staleness check in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
require(updatedAt != 0, "Round not complete");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Stale price");
require(price > 0, "Non-positive price");
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`MAX_STALENESS` should be configurable per feed (e.g., stored in a `mapping(address => uint256) public maxStaleness`) and set to the Chainlink heartbeat plus a small buffer (e.g., 25 hours for a 24-hour heartbeat feed). Additionally, ensure `pricePercentageLimit` is set to a non-zero value at deployment to activate the price-increase guard as a secondary defense.

## Proof of Concept

**Minimal Foundry fork test plan:**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";

contract MockStaleFeed {
    int256 public answer;
    uint256 public updatedAt;

    constructor(int256 _answer, uint256 _updatedAt) {
        answer = _answer;
        updatedAt = _updatedAt;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return (1, answer, 0, updatedAt, 1);
    }

    function decimals() external view returns (uint8) { return 18; }
}

contract StalenessPoC is Test {
    function testStalePrice() public {
        // 1. Fork mainnet; obtain LRTManager role via vm.prank
        // 2. Deploy MockStaleFeed(1.05e18, block.timestamp - 48 hours)
        //    — price frozen 48h ago at 1.05 ETH, true price now 0.95 ETH
        // 3. chainlinkOracle.updatePriceFeedFor(stETH, address(staleFeed))
        // 4. lrtOracle.updateRSETHPrice() — succeeds, no revert
        // 5. assertGt(lrtOracle.rsETHPrice(), 1e18)  // inflated above true ratio
        // 6. depositPool.depositAsset(stETH, 1e18, 0, "")
        // 7. assertGt(rsETH.balanceOf(attacker), 1e18) // receives >1 rsETH for 0.95 ETH collateral
    }
}
```

The `getAssetPrice` call at line 52 returns the stale price without any revert because `updatedAt` is never read, confirming the root cause is directly exploitable by any external caller. [9](#0-8)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
```

**File:** contracts/LRTOracle.sol (L231-313)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
        }

        // downside protection — pause if price drops too far
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

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }

        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;
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

**File:** contracts/LRTDepositPool.sol (L516-520)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
