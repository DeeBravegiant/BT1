### Title
Lack of Time Consideration in `_updateRsETHPrice` Price Threshold Check Causes DoS of Public Price Update and Stale-Price Dilution of Existing rsETH Holders - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle._updateRsETHPrice()` enforces a `pricePercentageLimit` guard (named `PriceAboveDailyThreshold`) that compares the raw price delta against `highestRsethPrice` with no consideration of how much time has elapsed since the last update. If `updateRSETHPrice()` goes uncalled for an extended period — due to keeper inactivity, network congestion, or any other delay — the legitimately accumulated staking-reward price increase can exceed the threshold in a single call, causing the public function to revert for every non-manager caller. During this DoS window the stored `rsETHPrice` is stale (lower than actual), and any depositor can exploit the stale price to receive more rsETH than they are entitled to, diluting existing holders.

### Finding Description
`LRTOracle._updateRsETHPrice()` contains the following guard:

```solidity
if (newRsETHPrice > highestRsethPrice) {
    uint256 priceDifference = newRsETHPrice - highestRsethPrice;
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 &&
        priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceIncreaseOffLimit) {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert PriceAboveDailyThreshold();
        }
    }
}
``` [1](#0-0) 

The error name `PriceAboveDailyThreshold` and the admin comment ("PricePercentageLimit for 1% is 1e16") make clear the intent is a *per-period* (daily) rate-of-change guard. However, the implementation compares the cumulative delta from `highestRsethPrice` to `newRsETHPrice` with no timestamp tracking. There is no stored `lastUpdateTimestamp`, no scaling of the allowed delta by elapsed time, and no early-return when the elapsed time is large. [2](#0-1) 

The public entry point `updateRSETHPrice()` is callable by anyone:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

The manager escape hatch `updateRSETHPriceAsManager()` exists but is restricted to the `MANAGER` role, so ordinary callers and keeper bots are blocked. [4](#0-3) 

When the public function is DoS'd, the stored `rsETHPrice` is not updated. `LRTDepositPool.getRsETHAmountToMint()` divides by the stale (lower) `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [5](#0-4) 

A lower denominator means depositors receive more rsETH than they are entitled to, diluting all existing holders.

### Impact Explanation
**High — Theft of unclaimed yield.**

When `updateRSETHPrice()` is DoS'd:
1. The stored `rsETHPrice` is stale and lower than the true value.
2. Any depositor calling `depositETH()` or `depositAsset()` receives an inflated rsETH mint amount at the expense of existing holders, who effectively have their accumulated staking yield diluted away.
3. Protocol fee minting inside `_updateRsETHPrice()` is also skipped for the entire DoS window, depriving the treasury of earned yield. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
**Medium.** The condition is triggered whenever the cumulative price increase since the last successful update exceeds `pricePercentageLimit`. With a typical limit of 1% (1e16) and staking APY of ~4%, the threshold is breached after roughly 90 days without a price update. Keeper bot outages, network congestion, or deliberate griefing (e.g., front-running every keeper tx with a revert-inducing call is not needed — simply not calling it suffices) can produce this window. The protocol has no on-chain mechanism to force a price update, so the DoS persists until a manager intervenes.

### Recommendation
Track the timestamp of the last successful price update and scale the allowed price delta by the elapsed time. For example:

```solidity
uint256 elapsed = block.timestamp - lastPriceUpdateTimestamp;
uint256 scaledLimit = pricePercentageLimit * elapsed / 1 days;
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 &&
    priceDifference > scaledLimit.mulWad(highestRsethPrice);
```

Alternatively, if the price increase is within the expected range for the elapsed time, skip the revert entirely (analogous to the `maxAge` fix applied in the referenced Mellow/Cantina report). At minimum, store `lastPriceUpdateTimestamp` and emit it so off-chain monitors can detect staleness.

### Proof of Concept
1. `pricePercentageLimit` is set to 1% (1e16). Staking rewards accrue at ~4% APY.
2. The keeper bot fails to call `updateRSETHPrice()` for 100 days. The true rsETH/ETH rate has increased by ~1.1% from `highestRsethPrice`.
3. Any EOA calls `updateRSETHPrice()`. Inside `_updateRsETHPrice()`, `priceDifference > pricePercentageLimit.mulWad(highestRsethPrice)` evaluates to `true` and the caller is not a manager → `revert PriceAboveDailyThreshold()`.
4. The stored `rsETHPrice` remains at the 100-day-old stale value.
5. A depositor calls `depositETH(minRSETHAmountExpected, "")`. `getRsETHAmountToMint` computes `(msg.value * assetPrice) / rsETHPrice` using the stale (lower) `rsETHPrice`, minting ~1.1% more rsETH than the depositor is entitled to.
6. Existing rsETH holders' share of the protocol TVL is diluted by the excess minted rsETH. The effect compounds with every deposit made during the DoS window.
7. The DoS continues until a manager calls `updateRSETHPriceAsManager()`. [1](#0-0) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L29-30)
```text
    uint256 public pricePercentageLimit;
    uint256 public highestRsethPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
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

**File:** contracts/LRTDepositPool.sol (L76-92)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
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
