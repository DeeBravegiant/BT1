### Title
Missing Chainlink Staleness Check Allows Oracle Price Abuse in Deposits and Withdrawals - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` values, performing zero staleness validation. This price is consumed directly by the deposit minting calculation and the withdrawal asset-amount calculation, both of which are reachable by any unprivileged user.

---

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` destructures the Chainlink response as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

`updatedAt` (position 4) and `answeredInRound` (position 5) are both discarded. No check of the form `block.timestamp - updatedAt > maxStaleness` or `answeredInRound < roundId` is performed. [1](#0-0) 

This price propagates upward through `LRTOracle.getAssetPrice()`: [2](#0-1) 

And is consumed in two critical user-facing paths:

**Path 1 – Deposit minting** (`LRTDepositPool.getRsETHAmountToMint`):
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

Called from `depositAsset()` and `depositETH()`, both publicly accessible. [4](#0-3) 

**Path 2 – Withdrawal asset amount** (`LRTWithdrawalManager.getExpectedAssetAmount`):
```
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [5](#0-4) 

Called from `initiateWithdrawal()`, publicly accessible. [6](#0-5) 

**Path 3 – rsETH price update** (`LRTOracle._getTotalEthInProtocol`): stale asset prices feed into `_updateRsETHPrice()`, which is callable by anyone via `updateRSETHPrice()`. [7](#0-6) 

Note: `ChainlinkOracleForRSETHPoolCollateral.getRate()` does check `answeredInRound < roundID` and `timestamp == 0`, but still omits the critical time-based check (`block.timestamp - timestamp > heartbeat`), leaving it partially vulnerable to the same class of issue. [8](#0-7) 

---

### Impact Explanation
**High – Theft of unclaimed yield / direct theft of user funds.**

**Scenario A (stale price above market):** If a Chainlink LST/ETH feed freezes at a price higher than the real market price, a depositor calling `depositAsset()` receives more rsETH than the fair value of their deposit. This dilutes all existing rsETH holders, constituting theft of their accrued yield.

**Scenario B (stale price below market):** If the feed freezes at a price lower than the real market price, a user calling `initiateWithdrawal()` has `getExpectedAssetAmount()` return a larger underlying amount (since the denominator `getAssetPrice(asset)` is artificially low). The user locks in a withdrawal entitlement for more LST than their rsETH is worth, draining assets from the pool at the expense of other depositors.

---

### Likelihood Explanation
**Medium.** Chainlink feeds have documented heartbeat windows (e.g., 24 hours for some LST/ETH feeds on mainnet). During periods of network congestion, oracle node downtime, or low price volatility (where the deviation threshold is not triggered), feeds can remain stale for extended periods within their heartbeat. This is a known, recurring condition — not a theoretical edge case. An attacker monitoring oracle update timestamps can identify and exploit the window without any privileged access.

---

### Recommendation
In `ChainlinkPriceOracle.getAssetPrice()`, capture `updatedAt` and revert if the price is stale:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale round");
require(updatedAt != 0, "Incomplete round");
require(block.timestamp - updatedAt <= MAX_ORACLE_DELAY, "Stale price");
```

`MAX_ORACLE_DELAY` should be set per-feed based on its documented heartbeat (e.g., 86400 seconds for a 24-hour heartbeat feed, with a small buffer). Store it alongside `assetPriceFeed` in the mapping.

Apply the same time-based check to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which already checks `answeredInRound` and `timestamp == 0` but is missing the `block.timestamp - timestamp` bound. [9](#0-8) 

---

### Proof of Concept

1. Assume stETH/ETH Chainlink feed last updated 25 hours ago (within its 24-hour heartbeat window, now stale) at price `1.05e18` while the real market price has dropped to `0.98e18`.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`.
4. `rsethAmountToMint = (100e18 * 1.05e18) / rsETHPrice` — attacker receives rsETH priced as if stETH is worth 1.05 ETH, not 0.98 ETH.
5. Attacker immediately redeems via `initiateWithdrawal()` for a different asset (e.g., ETH) at the correct rsETH price, extracting the ~7% premium from the pool at the expense of existing rsETH holders.
6. No privileged access is required; the only precondition is a stale Chainlink feed, which is an observable on-chain condition.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
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
