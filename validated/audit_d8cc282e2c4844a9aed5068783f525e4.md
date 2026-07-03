### Title
Missing Chainlink Oracle Data Validation Allows Stale Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice` consumes Chainlink `latestRoundData()` without validating the `updatedAt` staleness timestamp or checking that `price` is positive. This is the direct DeFi analog of the external report's "no authenticity verification" class: external data is accepted and acted upon without any integrity check. A stale or zero price propagates through `LRTOracle._updateRsETHPrice()` and corrupts the rsETH/ETH exchange rate used for all deposits.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but silently discards every return value except `price`: [1](#0-0) 

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Three validations are absent:
1. **No staleness check** — `updatedAt` is never compared to `block.timestamp`. A feed that has not been updated for hours (or days) is accepted as current.
2. **No positive-price check** — `int256 price` is cast directly to `uint256`. A zero price returns `0`; a negative price wraps to a near-`type(uint256).max` value, causing arithmetic overflow (revert) in downstream callers.
3. **No `answeredInRound >= roundId` check** — the round-completeness invariant is never verified.

This price is consumed by `LRTOracle.getAssetPrice`, which feeds `_getTotalEthInProtocol`, which feeds `_updateRsETHPrice`: [2](#0-1) 

`updateRSETHPrice()` is a **public, permissionless function**: [3](#0-2) 

Any external caller can trigger a price update at any time, including when the Chainlink feed is stale.

---

### Impact Explanation

**Stale price lower than actual market price:**
- `_getTotalEthInProtocol()` underestimates total ETH backing rsETH.
- `newRsETHPrice` is set below its true value.
- Users who deposit ETH or LSTs into L2 pools (which consume this rate via `CrossChainRateReceiver` → `RSETHPoolV3.viewSwapRsETHAmountAndFee`) receive **more wrsETH than they are entitled to**.
- Repeated exploitation drains the protocol's backing, constituting **theft of unclaimed yield** trending toward **protocol insolvency**.

**Zero price (feed returns 0):**
- The affected asset's entire TVL contribution is zeroed out in `_getTotalEthInProtocol`.
- rsETH is severely underpriced; depositors extract excess wrsETH.

**Negative price:**
- `uint256(negative_int256)` wraps to a huge value; subsequent `mulWad` overflows and reverts.
- `updateRSETHPrice()` becomes permanently reverting until the feed recovers — **temporary freeze of the price-update mechanism**, blocking new deposits that depend on a fresh rate.

Impact classification: **High — theft of unclaimed yield / protocol insolvency** (stale/zero path); **Medium — temporary fund freeze** (negative-price path).

---

### Likelihood Explanation

- Chainlink feeds can go stale during network congestion, sequencer downtime (relevant for any L2 deployment of the oracle), or during a feed migration.
- `updateRSETHPrice()` is public and callable by anyone, so an attacker can deliberately time the call to coincide with a known stale window.
- LST/ETH feeds (stETH, rETH, swETH, sfrxETH) are the exact feeds wired into `ChainlinkPriceOracle` via `updatePriceFeedFor`.

Likelihood: **Medium**.

---

### Recommendation

Add staleness and validity guards inside `getAssetPrice`:

```solidity
uint256 constant MAX_STALENESS = 3600; // 1 hour, tune per feed

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound)
        = priceFeed.latestRoundData();

    require(price > 0, "ChainlinkPriceOracle: non-positive price");
    require(updatedAt != 0 && block.timestamp - updatedAt <= MAX_STALENESS,
            "ChainlinkPriceOracle: stale price");
    require(answeredInRound >= roundId, "ChainlinkPriceOracle: incomplete round");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Per-feed staleness thresholds should be stored in a mapping, since heartbeat intervals differ across feeds.

---

### Proof of Concept

1. Chainlink stETH/ETH feed heartbeat is 24 h; the last update was 23 h 50 min ago. The stale price is 0.97 ETH (actual market: 1.00 ETH).
2. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no role required).
3. `getAssetPrice(stETH)` returns `0.97e18` — no staleness check fires.
4. `_getTotalEthInProtocol()` underestimates total ETH by ~3% of the stETH TVL.
5. `newRsETHPrice` is set ~3% below its true value.
6. Attacker immediately calls `RSETHPoolV3.deposit{value: 10 ether}(...)` on an L2 pool whose oracle has just been updated via `CrossChainRateReceiver`.
7. `viewSwapRsETHAmountAndFee` computes `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` using the depressed rate, minting ~3% excess wrsETH.
8. Attacker redeems wrsETH for rsETH at 1:1 via the wrapper, extracting value from honest depositors. [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```
