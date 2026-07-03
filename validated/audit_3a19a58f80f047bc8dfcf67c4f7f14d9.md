### Title
Chainlink Oracle Return Values Not Validated in `ChainlinkPriceOracle.getAssetPrice()`, Enabling Incorrect rsETH Price Computation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no validation for a zero/negative answer, an incomplete round (`updatedAt == 0`), or a stale round (`answeredInRound < roundId`). The unvalidated price directly feeds `LRTOracle._updateRsETHPrice()`, which sets the global `rsETHPrice` used for all deposits and withdrawals. The same codebase already contains `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which validates all three conditions, demonstrating the developers are aware of the pattern but did not apply it consistently.

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` calls `latestRoundData()` and silently discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Three distinct failure modes are unguarded:

1. **Negative price** — Chainlink can return `answer < 0` during extreme market dislocations. In Solidity 0.8.x, `uint256(negative_int256)` does not revert; it wraps to a value near `2^256`, making `assetER` astronomically large.
2. **Incomplete round** — `updatedAt == 0` signals a round that was started but never completed. No check exists.
3. **Stale round** — `answeredInRound < roundId` signals the answer was carried over from a prior round. No check exists.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository validates all three:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unvalidated price propagates as follows:

- `ChainlinkPriceOracle.getAssetPrice()` is called by `LRTOracle.getAssetPrice()`. [3](#0-2) 

- `LRTOracle.getAssetPrice()` is called inside `_getTotalEthInProtocol()`, which sums `assetER * totalAssetAmt` for every supported asset. [4](#0-3) 

- `_getTotalEthInProtocol()` feeds `_updateRsETHPrice()`, which computes and stores the global `rsETHPrice`. [5](#0-4) 

- `rsETHPrice` is also read directly by `LRTWithdrawalManager._createUnlockParams()` to determine how many assets a withdrawer receives. [6](#0-5) 

### Impact Explanation

**Stale price (answeredInRound < roundId):** A stale price that is materially lower than the true price causes `totalETHInProtocol` to be understated, producing a deflated `rsETHPrice`. Any depositor who calls `updateRSETHPrice()` (public, no access control) immediately before depositing receives more rsETH than their assets are worth, diluting all existing rsETH holders — a theft of unclaimed yield. A stale price that is materially higher overstates `rsETHPrice`, causing withdrawers to receive fewer assets than they are owed (temporary freeze of funds).

**Negative price (price ≤ 0):** `uint256(negative_int256)` wraps to a value near `2^256`. `totalETHInProtocol` overflows or becomes astronomically large. If `pricePercentageLimit == 0` (no threshold guard), `rsETHPrice` is set to a near-infinite value. Subsequent depositors receive near-zero rsETH; existing holders who withdraw receive far more assets than they deposited, draining the protocol (insolvency).

**Zero price:** `assetER = 0` causes the affected asset's entire TVL to be excluded from `totalETHInProtocol`, artificially deflating `rsETHPrice` and triggering the downside-protection auto-pause, temporarily freezing all deposits and withdrawals.

**Impact classification:** High — theft of unclaimed yield (stale price scenario); Medium — temporary freezing of funds (zero price scenario).

### Likelihood Explanation

Chainlink feeds can return stale data during periods of network congestion, sequencer downtime, or when the deviation threshold and heartbeat have not been met. The `updateRSETHPrice()` function is public and callable by any address with no access restriction, meaning any user can trigger a price update at the exact moment a stale or anomalous Chainlink round is active. [7](#0-6) 

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a maximum staleness threshold (e.g., `block.timestamp - updatedAt > MAX_STALENESS`) appropriate to each feed's heartbeat.

### Proof of Concept

1. Chainlink's ETH/USD or LST/ETH feed enters a stale round (`answeredInRound < roundId`) due to network congestion, returning a price 5% below the true market price.
2. An attacker observes the stale round on-chain.
3. The attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control), which calls `_getTotalEthInProtocol()` → `ChainlinkPriceOracle.getAssetPrice()` → returns the stale, deflated price without any revert.
4. `rsETHPrice` is set 5% below its true value and stored on-chain.
5. The attacker immediately deposits assets via `LRTDepositPool`, receiving ~5% more rsETH than their deposit is worth at the true price.
6. When the Chainlink feed recovers and `rsETHPrice` is updated to the correct value, the attacker's rsETH is worth 5% more than they paid, at the expense of all existing rsETH holders.

The entry path is fully permissionless: `updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `ChainlinkPriceOracle.getAssetPrice()` → unvalidated `latestRoundData()`. [8](#0-7)

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L231-250)
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

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
