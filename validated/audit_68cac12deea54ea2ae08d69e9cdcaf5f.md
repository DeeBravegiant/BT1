Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice` Enables Phantom Fee Minting Against Inflated TVL — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, accepting any price regardless of age. When a supported LST's Chainlink feed is stale and its last reported price exceeds the current true price, `_getTotalEthInProtocol` overstates TVL. The fee-minting branch in `_updateRsETHPrice` treats the phantom TVL increase as real yield and mints rsETH to the treasury, permanently diluting all rsETH holders.

## Finding Description

**Root cause — `ChainlinkPriceOracle.getAssetPrice`:**

`latestRoundData()` returns five values; the contract destructures only `price` and silently drops `updatedAt` and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
``` [1](#0-0) 

There is no `require(updatedAt >= block.timestamp - MAX_STALENESS)` and no `require(answeredInRound >= roundId)` guard. The same repository's pool-side oracle performs both checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The production `ChainlinkPriceOracle` used for LST pricing has no equivalent protection. [3](#0-2) 

**Fee-minting path:**

`_getTotalEthInProtocol` iterates every supported asset and calls `getAssetPrice(asset)`, routing to `ChainlinkPriceOracle.getAssetPrice` for Chainlink-backed LSTs. The stale inflated price is multiplied by total deposited amounts, overstating `totalETHInProtocol`. [4](#0-3) 

In `_updateRsETHPrice`, if `totalETHInProtocol > previousTVL`, the difference is treated as real yield and a protocol fee is computed: [5](#0-4) 

The fee is then minted to the treasury as rsETH: [6](#0-5) 

**Existing guards and why they are insufficient:**

1. **`pricePercentageLimit`**: If `pricePercentageLimit == 0` (its default/unset value), the price-threshold check at lines 256–266 never reverts for non-manager callers. Even when set, a stale price that inflates TVL by an amount within the configured limit passes the check and mints fees. The revert at line 264 does occur before the actual mint (lines 299–308), so only out-of-limit spikes are blocked; in-limit phantom yield is not. [7](#0-6) 

2. **`maxFeeMintAmountPerDay`**: If set to zero, `_checkAndUpdateDailyFeeMintLimit` reverts any fee mint. However, for the protocol to collect fees at all, this must be set to a non-zero value, at which point phantom fees can be minted up to that daily cap. [8](#0-7) 

3. **`updateRSETHPrice()` is permissionless**: Any EOA can trigger the fee-minting path at will. [9](#0-8) 

## Impact Explanation

When a Chainlink feed for a supported LST is stale and its last reported price exceeds the current true price, calling `updateRSETHPrice()` causes the protocol to mint rsETH to the treasury against TVL that does not exist. Every existing rsETH holder's share of the underlying ETH is permanently diluted. This is a direct, concrete instance of **High — Theft of unclaimed yield**: yield that would otherwise accrue to rsETH holders is instead captured by the treasury via phantom fee minting.

## Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 24 h for many LST/ETH feeds). Network congestion, Chainlink node issues, or a deprecated feed can cause staleness. The attack requires no special role, no capital, and no front-running — only the ability to call a public function when a feed is stale and the last reported price exceeds the current true price. This is a realistic, non-negligible operational scenario.

## Recommendation

Add a configurable `MAX_STALENESS` constant and validate both `updatedAt` and `answeredInRound` in `ChainlinkPriceOracle.getAssetPrice`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt >= block.timestamp - MAX_STALENESS, "Price too old");
require(price > 0, "Invalid price");
```

## Proof of Concept

```solidity
// Foundry test (local, no mainnet fork required)
contract StalePriceTest is Test {
    MockChainlinkFeed feed;
    ChainlinkPriceOracle chainlinkOracle;
    LRTOracle lrtOracle;
    // ... minimal protocol stack setup

    function test_staleOraclePhantomFee() public {
        // 1. Configure feed: price = 1.05e18, updatedAt = block.timestamp - 48 hours
        feed.setAnswer(1.05e18, block.timestamp - 48 hours);

        // 2. Advance time; true market price has dropped to 1.00e18
        //    but the feed has NOT been updated — it is stale

        // 3. Record treasury rsETH balance before
        uint256 treasuryBefore = rsETH.balanceOf(treasury);

        // 4. Unprivileged caller triggers updateRSETHPrice
        vm.prank(address(0xdead));
        lrtOracle.updateRSETHPrice();

        // 5. Treasury received rsETH despite no real yield
        assertGt(rsETH.balanceOf(treasury), treasuryBefore);
    }
}
```

The mock feed returns a stale inflated price; `_getTotalEthInProtocol` overstates TVL; `rewardAmount > 0`; fee is minted. No real yield accrued. The test requires `pricePercentageLimit == 0` (default) or the phantom inflation to be within the configured limit, and `maxFeeMintAmountPerDay > 0`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L205-206)
```text
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
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

**File:** contracts/LRTOracle.sol (L299-308)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
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
