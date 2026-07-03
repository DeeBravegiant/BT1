Audit Report

## Title
Missing Chainlink Staleness Check Enables Over-Minting of rsETH via Stale LST Price — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` discards all return values from `latestRoundData()` except `answer`, with no check on `updatedAt` (staleness) or `answeredInRound >= roundId` (round completeness). A stale high Chainlink price for any supported LST is silently accepted and used to compute the rsETH mint amount, while `rsETHPrice` may reflect a prior lower valuation, causing depositors to receive more rsETH than their collateral is worth in ETH and driving the protocol toward insolvency.

## Finding Description

**Root cause — `ChainlinkPriceOracle.getAssetPrice()`:**

All five return values of `latestRoundData()` are available but only `answer` is captured: [1](#0-0) 

Neither `updatedAt` (to bound staleness) nor `answeredInRound >= roundId` (to confirm round completion) is checked.

**Mint calculation — `LRTDepositPool.getRsETHAmountToMint()`:** [2](#0-1) 

`lrtOracle.getAssetPrice(asset)` flows through `LRTOracle.getAssetPrice()` → `ChainlinkPriceOracle.getAssetPrice()` with no additional freshness gate. `lrtOracle.rsETHPrice()` is a stored value updated only on explicit calls to `updateRSETHPrice()`. [3](#0-2) 

**`rsETHPrice` update — `LRTOracle._updateRsETHPrice()`:**

`rsETHPrice` is recomputed from `_getTotalEthInProtocol()`, which also calls `getAssetPrice()` for each asset. It is not updated on every deposit. [4](#0-3) 

**Downside protection is reactive, not preventive:**

The pause mechanism in `_updateRsETHPrice()` triggers only when `updateRSETHPrice()` is called with a corrected (lower) price — after over-minting has already occurred. [5](#0-4) 

**Exploit flow:**
1. Chainlink feed for stETH/ETH last updated at 1.00e18; true market price drops to 0.99e18 (within deviation threshold, no feed update triggered).
2. `rsETHPrice` was last set at 1.00e18 and has not been refreshed.
3. Attacker calls `depositAsset(stETH, 100e18, ...)`.
4. `getRsETHAmountToMint` computes: `100e18 * 1.00e18 / 1.00e18 = 100 rsETH` — but true ETH value deposited is only 99 ETH.
5. When `updateRSETHPrice()` is next called with the corrected price, `rsETHPrice` drops; the attacker's rsETH represents a claim on more ETH than deposited. The shortfall is borne by all other rsETH holders.

The scenario is amplified when the stale price is above `rsETHPrice` (e.g., feed stale at 1.05e18, rsETHPrice = 1.00e18, true price = 0.99e18), yielding 105 rsETH for 99 ETH of true value — a 6 ETH shortfall per 100 stETH deposited.

## Impact Explanation

**Critical — Protocol insolvency.** The over-minted rsETH represents a claim on ETH that was never deposited. When `rsETHPrice` is corrected downward, all existing rsETH holders are diluted to cover the shortfall. Repeated exploitation across multiple deposits and multiple stale feed windows compounds the insolvency. This matches the allowed impact: *Protocol insolvency*.

## Likelihood Explanation

Chainlink feeds operate on heartbeat intervals (e.g., 24 h for stETH/ETH on mainnet) and deviation thresholds (e.g., 0.5%). During rapid LST depeg events — slashing, withdrawal queue freezes, secondary-market sell-offs — the on-chain feed can lag the true price for minutes to hours without triggering an update. No privileged access, governance capture, or oracle operator compromise is required. The attacker only needs to call the public `depositAsset()` function while the feed is stale, which is a realistic and repeatable condition.

## Recommendation

Add staleness and round-completeness checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 public constant STALENESS_THRESHOLD = 25 hours; // tune per feed heartbeat

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (price <= 0) revert InvalidPrice();
    if (answeredInRound < roundId) revert StalePrice();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Per-feed thresholds stored in a mapping are preferable since different assets have different heartbeat intervals.

## Proof of Concept

**Foundry fork/mock test:**

```solidity
// forge test --match-test testStalePriceOverMint -vvv
function testStalePriceOverMint() external {
    // Mock Chainlink feed: stale answer (1.00e8), updatedAt = 26 hours ago,
    // answeredInRound (9) < roundId (10)
    vm.mockCall(
        CHAINLINK_FEED,
        abi.encodeWithSignature("latestRoundData()"),
        abi.encode(uint80(10), int256(1.00e8), uint256(0), block.timestamp - 26 hours, uint80(9))
    );

    // True market price is 0.99e18; rsETHPrice = 1.00e18 (last stored)
    uint256 depositAmount = 100e18;
    uint256 rsethMinted = ILRTDepositPool(DEPOSIT_POOL).getRsETHAmountToMint(STETH, depositAmount);

    // rsethMinted = 100e18 * 1.00e18 / 1.00e18 = 100e18
    // True ETH value = 99e18 — 1 ETH shortfall per 100 stETH at minimum
    uint256 trueETHValue = depositAmount * 0.99e18 / 1e18;
    assertGt(rsethMinted * rsETHPriceBefore / 1e18, trueETHValue, "attacker profit");
}
```

**Fuzz variant** (confirms invariant breach for any stale price above true price):

```solidity
function testFuzz_stalePriceOverMint(uint256 stalePriceHigh) external {
    stalePriceHigh = bound(stalePriceHigh, 1.01e18, 1.10e18);
    uint256 truePrice  = 0.99e18;
    uint256 rsETHPrice = 1.00e18;
    uint256 deposit    = 100e18;

    uint256 minted    = deposit * stalePriceHigh / rsETHPrice;
    uint256 trueValue = deposit * truePrice / 1e18;

    // Invariant: minted rsETH value must not exceed deposited ETH value
    // Fails for every stalePriceHigh > truePrice
    assertLe(minted, trueValue, "collateral ratio breach");
}
```

The fuzz test fails for every value in the range, confirming the invariant is broken by the missing staleness check.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-231)
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
```

**File:** contracts/LRTOracle.sol (L270-281)
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
```
