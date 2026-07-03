### Title
Stale Chainlink Price Accepted Without Validation Allows Anyone to Lock an Incorrect `rsETHPrice` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt` and `answeredInRound`, accepting stale prices without any staleness or round-completeness check. Because `LRTOracle.updateRSETHPrice()` is a public, permissionless function that feeds directly from this oracle, any unprivileged caller can trigger a `rsETHPrice` update at a moment when the underlying Chainlink feed is stale, locking an incorrect exchange rate into storage. This stored rate then governs rsETH minting in `LRTDepositPool` and withdrawal payouts in `LRTWithdrawalManager`, and is propagated cross-chain via `RSETHMultiChainRateProvider` / `RSETHRateProvider`.

---

### Finding Description

**Root cause — missing staleness guard in `ChainlinkPriceOracle`:**

`contracts/oracles/ChainlinkPriceOracle.sol` lines 49–55 read:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt & answeredInRound silently dropped
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. Only `answer` is used; `updatedAt` (age of the price) and `answeredInRound` (round completeness) are discarded. The same codebase's `ChainlinkOracleForRSETHPoolCollateral` (used for pool collateral) explicitly performs these checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

This inconsistency confirms the protocol is aware of the requirement but failed to apply it to the L1 core oracle.

**Permissionless trigger — `updateRSETHPrice()` is public:**

`contracts/LRTOracle.sol` line 87:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

No role check. Any EOA or contract can call this at any time. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over all supported LST assets and calls `getAssetPrice(asset)` — routed through the staleness-blind `ChainlinkPriceOracle` — to compute `totalETHInProtocol`. The result is divided by `rsethSupply` to produce `newRsETHPrice`, which is then written to storage as `rsETHPrice`.

**Stored rate propagated to minting and withdrawals:**

- `LRTDepositPool._beforeDeposit()` calls `ILRTOracle.getRsETHAmountToMint()`, which uses the stored `rsETHPrice`.
- `LRTWithdrawalManager._calculatePayoutAmount()` uses `rsETHPrice` directly to compute withdrawal disbursements.
- `RSETHMultiChainRateProvider.updateRate()` and `RSETHRateProvider.updateRate()` read `ILRTOracle(rsETHPriceOracle).rsETHPrice()` and broadcast it cross-chain to L2 pools, where it governs wrsETH minting.

---

### Impact Explanation

**Scenario A — stale price is lower than true value (within `pricePercentageLimit`):**
An attacker calls `updateRSETHPrice()` while a Chainlink LST feed is stale at a temporarily depressed price. `rsETHPrice` is written lower than the true rate. The attacker then deposits ETH/LST into `LRTDepositPool` and receives more rsETH than the assets are worth. When the oracle recovers and `rsETHPrice` is corrected upward, the attacker's rsETH is worth more than deposited — a direct theft of yield from existing rsETH holders (dilution of the pool).

**Scenario B — stale price triggers auto-pause:**
If the stale price drop exceeds `pricePercentageLimit`, `_updateRsETHPrice()` calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` on the oracle itself, freezing all deposits and withdrawals until an admin manually unpauses. An attacker can deliberately trigger this at a moment of Chainlink latency to cause a temporary freeze of user funds.

**Scenario C — cross-chain propagation:**
After locking in a stale `rsETHPrice`, anyone can call `RSETHMultiChainRateProvider.updateRate()` (also permissionless) to broadcast the incorrect rate to all L2 pools, causing incorrect wrsETH minting across multiple chains until the rate is corrected.

Impact classification: **High** (theft of unclaimed yield / dilution) and **Medium** (temporary freezing of funds via forced auto-pause).

---

### Likelihood Explanation

Chainlink feeds can lag during periods of high network congestion, sequencer downtime (for L2-sourced feeds), or during rapid LST price movements. The attacker does not need to manipulate Chainlink — they only need to observe a moment of staleness and call the permissionless `updateRSETHPrice()`. This is a realistic, low-cost action requiring no capital at risk for the trigger itself. Likelihood: **Low-Medium** (requires a Chainlink staleness window, which occurs periodically in practice).

---

### Recommendation

Apply the same staleness and round-completeness checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();
    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert PriceStale();
    if (price <= 0) revert InvalidPrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding an access-control modifier (e.g., `onlyLRTManager`) or a minimum time-between-updates guard to `updateRSETHPrice()` to prevent permissionless triggering of price snapshots.

---

### Proof of Concept

1. Observe that a Chainlink LST/ETH feed (e.g., stETH/ETH) has not been updated for longer than its heartbeat (e.g., 24 h), returning a stale, lower-than-current price.
2. Call `LRTOracle.updateRSETHPrice()` from any EOA. The function is `public whenNotPaused` with no role check.
3. Inside `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the stale low price without reverting.
4. `totalETHInProtocol` is computed lower than the true value; `newRsETHPrice` is set lower than the true rate and written to `rsETHPrice`.
5. Attacker calls `LRTDepositPool.depositAsset(stETH, largeAmount, 0, "")`. The minting calculation uses the now-depressed `rsETHPrice`, issuing more rsETH than the deposited assets are worth.
6. When `updateRSETHPrice()` is next called with a fresh Chainlink price, `rsETHPrice` recovers to its true value. The attacker's rsETH is now worth more than deposited, at the expense of existing holders.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-251)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
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
