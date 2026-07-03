### Title
`ChainlinkPriceOracle.getAssetPrice()` Accepts Stale Chainlink Data With No Validation, Corrupting rsETH Price and Enabling Yield Theft — (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` on a single Chainlink feed per asset but discards every validation field (`updatedAt`, `answeredInRound`, `startedAt`). When the feed goes stale, the corrupted price propagates through `LRTOracle._getTotalEthInProtocol()` into `_updateRsETHPrice()`, setting an incorrect `rsETHPrice`. Any caller can trigger this path via the public `updateRSETHPrice()`. An attacker who times a deposit around a stale low price receives more rsETH than deserved, diluting existing holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the `price` field from `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are available — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — but only `answer` is consumed. There is no check that `updatedAt != 0`, no heartbeat/staleness window check, and no `answeredInRound >= roundId` guard.

The protocol's own `ChainlinkOracleForRSETHPoolCollateral` (used for L2 pool collateral) demonstrates the correct pattern in the same codebase:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle` is the oracle registered for supported LST assets (e.g., stETH, ETHx) in `LRTOracle.assetPriceOracle`. The stale price flows through:

1. `LRTOracle.getAssetPrice(asset)` → delegates to `ChainlinkPriceOracle.getAssetPrice()`
2. `LRTOracle._getTotalEthInProtocol()` → sums `totalAssetAmt * assetER` for all supported assets
3. `LRTOracle._updateRsETHPrice()` → computes `newRsETHPrice = totalETHInProtocol / rsethSupply`
4. Stores the corrupted value in `rsETHPrice`

`updateRSETHPrice()` has no access control — it is callable by any address.

---

### Impact Explanation

**Stale-low scenario (attacker-profitable):**

When a Chainlink LST/ETH feed goes stale at a price below the true market rate (e.g., during network congestion or a sequencer hiccup), an attacker calls the public `updateRSETHPrice()`. `_getTotalEthInProtocol()` undervalues the protocol's TVL, so `newRsETHPrice` is set below its true value. The attacker then deposits ETH or LST via `LRTDepositPool.depositETH()` / `depositAsset()`, which mints rsETH at the deflated price — receiving more rsETH than the deposited value warrants. When the oracle recovers and `rsETHPrice` rises back to its true level, the attacker's rsETH is worth more ETH than was deposited. The surplus is extracted from existing holders' share of the pool.

**Stale-low + pause scenario:**

If the stale price drop exceeds `pricePercentageLimit`, `_updateRsETHPrice()` pauses both `LRTDepositPool` and `LRTWithdrawalManager`, temporarily freezing all user funds until an admin manually unpauses.

**Impact classification:** High — theft of unclaimed yield / value from existing rsETH holders; Medium — temporary freezing of funds via spurious pause.

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 24 h for stETH/ETH on mainnet) and can lag during periods of low L1 activity or sequencer downtime. The attacker needs only to:

1. Monitor the Chainlink feed's `updatedAt` timestamp off-chain.
2. Observe that the last reported price is stale and below the true market rate.
3. Call the permissionless `updateRSETHPrice()`.
4. Immediately deposit via `depositETH()` or `depositAsset()`.

No privileged access, no governance capture, and no brute-force is required. The entry path is fully permissionless.

---

### Recommendation

Apply the same staleness guards already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: add a configurable heartbeat check
    // if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a configurable per-asset maximum staleness window (heartbeat) so that feeds that have not updated within their expected interval are rejected rather than silently accepted.

---

### Proof of Concept

**Root cause — missing validation in `ChainlinkPriceOracle.getAssetPrice()`:** [1](#0-0) 

**Correct pattern already used elsewhere in the same codebase:** [2](#0-1) 

**Stale price propagates into total ETH calculation:** [3](#0-2) 

**Corrupted total ETH sets the rsETH price:** [4](#0-3) 

**Public entry point — no access control:** [5](#0-4) 

**Attack sequence:**

1. Off-chain: observe that the stETH/ETH Chainlink feed's `updatedAt` is stale and its last reported price is below the true market rate.
2. Call `LRTOracle.updateRSETHPrice()` — permissionless, sets `rsETHPrice` to a deflated value.
3. Call `LRTDepositPool.depositETH{value: X}(0, "")` — mints rsETH at the deflated price, receiving more rsETH than `X` ETH is worth at the true rate.
4. Wait for the Chainlink feed to update and `rsETHPrice` to recover.
5. Initiate withdrawal — the attacker's rsETH redeems for more ETH than was deposited; the difference is taken from existing holders' proportional share.

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

**File:** contracts/LRTOracle.sol (L230-250)
```text
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
