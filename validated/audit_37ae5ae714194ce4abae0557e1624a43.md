### Title
Unvalidated Raw Chainlink Response Passthrough in Oracle Chain Enables Stale-Price-Triggered Protocol Freeze - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` forwards the raw return value of `latestRoundData()` directly into the rsETH price calculation chain with no staleness check, no round-completeness check, and no non-negative price guard. Because `LRTOracle.updateRSETHPrice()` is a public, permissionless function, any caller can trigger a price update at a moment when a Chainlink feed is stale, causing the protocol to compute an incorrect rsETH price and, if the deviation is large enough, auto-pause deposits and withdrawals.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and silently discards every field except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol  line 52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is performed on:
- `updatedAt` — whether the answer is fresh
- `answeredInRound >= roundId` — whether the round is complete
- `price > 0` — whether the answer is valid

This raw, unvalidated price is then passed up the call chain without any additional sanitization:

```
ChainlinkPriceOracle.getAssetPrice()
  → LRTOracle.getAssetPrice()          (line 157 — no validation added)
    → LRTOracle._getTotalEthInProtocol() (line 339 — used directly in TVL sum)
      → LRTOracle._updateRsETHPrice()   (line 250 — sets rsETHPrice)
        ← LRTOracle.updateRSETHPrice()  (line 87 — public, no role restriction)
```

The protocol's own pool-side oracle wrapper, `ChainlinkOracleForRSETHPoolCollateral`, demonstrates the correct pattern that is absent here:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  lines 30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0)            revert IncompleteRound();
if (ethPrice <= 0)             revert InvalidPrice();
```

The same three guards are entirely missing from `ChainlinkPriceOracle`.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds.**

`LRTOracle._updateRsETHPrice()` contains automatic downside protection:

```solidity
// contracts/LRTOracle.sol  lines 270-281
if (newRsETHPrice < highestRsethPrice) {
    ...
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
```

If a stale Chainlink feed reports a price that is sufficiently lower than the true value, the computed `newRsETHPrice` will fall below `highestRsethPrice` by more than `pricePercentageLimit`. The auto-pause fires, freezing `LRTDepositPool` and `LRTWithdrawalManager` for all users until an admin manually unpauses. Because `updateRSETHPrice()` is public, any external actor can deliberately time this call to coincide with a stale feed window.

Additionally, if the stale price is within the percentage limit (or `pricePercentageLimit == 0`), the incorrect price is committed to `rsETHPrice`, causing all subsequent deposits in `LRTDepositPool` to mint rsETH at the wrong rate — a share/asset mis-accounting impact.

---

### Likelihood Explanation

**Likelihood: Medium.**

Chainlink feeds go stale during L1 network congestion, sequencer downtime on L2, or when the deviation threshold is not crossed for an extended period. These are well-documented, recurring conditions. The entry point (`updateRSETHPrice()`) is public and requires no privilege. An attacker only needs to monitor the `updatedAt` timestamp of any supported asset's Chainlink feed and call `updateRSETHPrice()` during a stale window.

---

### Recommendation

Apply the same three guards already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound)
    = priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0)            revert IncompleteRound();
if (price <= 0)                revert InvalidPrice();
// optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

---

### Proof of Concept

**Root cause — unvalidated passthrough:** [1](#0-0) 

**Correct pattern present elsewhere in the same repo:** [2](#0-1) 

**Unvalidated price flows into TVL calculation:** [3](#0-2) [4](#0-3) 

**Public entry point — no role restriction:** [5](#0-4) 

**Auto-pause triggered by stale-price-induced drop:** [6](#0-5) 

**Attack sequence:**

1. Attacker monitors `updatedAt` on any Chainlink feed registered in `ChainlinkPriceOracle` (e.g., stETH/ETH).
2. Feed goes stale (network congestion, deviation threshold not crossed). `updatedAt` is old; reported price drifts below true value.
3. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no role check).
4. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice()`, which returns the stale, lower price without reverting.
5. `newRsETHPrice` is computed lower than `highestRsethPrice` by more than `pricePercentageLimit`.
6. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called automatically.
7. All user deposits and withdrawals are frozen until an admin intervenes.

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
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
```

**File:** contracts/LRTOracle.sol (L331-344)
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

```
