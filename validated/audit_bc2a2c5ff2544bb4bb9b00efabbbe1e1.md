Audit Report

## Title
Missing Chainlink `updatedAt` Staleness Validation Enables Stale-Price Arbitrage - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` return value, imposing no time-bound on how old a price can be. This stale price flows directly into `LRTDepositPool.depositAsset()` and `LRTWithdrawalManager.initiateWithdrawal()`, both permissionless entry points, enabling an attacker to mint excess rsETH against a depegged LST priced at the last stale (inflated) Chainlink value and redeem it for full-value ETH, directly stealing from honest depositors.

## Finding Description
**Root cause — `contracts/oracles/ChainlinkPriceOracle.sol` L52:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt silently dropped
```

The interface declares all five return values including `updatedAt` (L17), but the implementation binds only `price` and performs zero staleness validation. [1](#0-0) 

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` in the same repository does check `answeredInRound < roundID` and `timestamp == 0`, confirming the team is aware of the pattern but omitted it from the main oracle. [2](#0-1) 

**Propagation path:**

`LRTOracle.getAssetPrice()` delegates unconditionally to the oracle with no additional guard: [3](#0-2) 

The stale price then reaches two critical permissionless paths:

- **Deposit:** `LRTDepositPool.getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. An inflated `getAssetPrice(asset)` directly inflates the rsETH minted. [4](#0-3) 

- **Withdrawal:** `LRTWithdrawalManager.getExpectedAssetAmount()` computes `underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)`. A deflated (stale low) `getAssetPrice(asset)` inflates the asset payout. [5](#0-4) 

Both `depositAsset()` and `initiateWithdrawal()` carry no role restriction beyond `whenNotPaused` and asset-support checks. [6](#0-5) [7](#0-6) 

**Why existing guards fail:**

`LRTOracle._updateRsETHPrice()` has a `pricePercentageLimit` that can auto-pause the protocol when the computed rsETH price moves beyond a threshold. However, this guard only fires when `updateRSETHPrice()` is explicitly called. If the Chainlink feed is stale, `getAssetPrice()` returns the same frozen value on every call, so `_updateRsETHPrice()` computes an unchanged `rsETHPrice` — the price-change check never triggers and no pause occurs. The stored `rsETHPrice` and the live `getAssetPrice()` both reflect the same stale pre-depeg value, leaving the minting ratio exploitable. [8](#0-7) 

## Impact Explanation
**Critical — Direct theft of user funds.**

When a Chainlink LST/ETH feed goes stale while the real market price has dropped (e.g., an stETH depeg), an attacker can:
1. Buy the depegged LST cheaply on the open market.
2. Deposit it via `depositAsset()`, receiving rsETH priced at the stale (inflated) rate.
3. Redeem the excess rsETH for ETH via `initiateWithdrawal()` or `instantWithdrawal()` at fair value.

The protocol is left holding overvalued collateral; losses are socialised onto honest depositors. This matches the allowed impact: **Critical — Direct theft of any user funds**.

## Likelihood Explanation
**Medium.** Chainlink feed staleness is a documented, recurring real-world event (network congestion, oracle node failure, extreme volatility). LST depeg events (stETH March 2023, rETH) have occurred historically. Because the protocol imposes no time-bound at all, any period of oracle inactivity is immediately exploitable by any EOA with no special permissions. The attack is repeatable for as long as the feed remains stale.

## Recommendation
Add a per-asset heartbeat mapping and a staleness check inside `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
mapping(address asset => uint256 heartbeat) public assetHeartbeat;
uint256 public constant STALENESS_BUFFER = 1 hours;

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();

    uint256 heartbeat = assetHeartbeat[asset];
    if (heartbeat > 0 && block.timestamp - updatedAt > heartbeat + STALENESS_BUFFER) {
        revert StalePriceFeed(asset);
    }

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Set each asset's heartbeat to match the Chainlink feed's documented update frequency (e.g., 86 400 s for daily feeds, 3 600 s for hourly feeds). The buffer should be kept small (≤ 1 hour).

## Proof of Concept
**Foundry fork test outline:**

1. Fork Ethereum mainnet at a block where the stETH/ETH Chainlink feed is live.
2. Use `vm.mockCall` to freeze `latestRoundData()` for the stETH feed at its current answer but with `updatedAt = block.timestamp - 30 hours` (simulating a 30-hour stale feed).
3. Separately, reduce the real stETH market price by 5% (mock a DEX pool or use a second oracle).
4. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` from an attacker EOA.
5. Assert that `rsethAmountToMint` is computed using the stale (inflated) price — attacker receives rsETH worth more than the 950 ETH they paid for the stETH.
6. Call `LRTWithdrawalManager.instantWithdrawal(ETH, rsETHAmount, "")` (if instant withdrawal is enabled) or `initiateWithdrawal` + advance blocks + `completeWithdrawal`.
7. Assert attacker's net ETH gain exceeds zero, confirming fund theft from the protocol.

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

**File:** contracts/LRTOracle.sol (L252-281)
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
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
