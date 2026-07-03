Audit Report

## Title
Missing Chainlink Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Enables Stale-Price rsETH Minting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `answeredInRound`, and `roundId`, accepting stale Chainlink prices without any validity check. Because `LRTOracle.updateRSETHPrice()` is a public, permissionless function that uses this oracle to set the protocol-wide `rsETHPrice`, an attacker can lock in a depressed stale price and immediately deposit a non-stale asset to mint excess rsETH, extracting value from existing holders.

## Finding Description

**Root cause — missing validation in `ChainlinkPriceOracle`:**

`getAssetPrice()` reads only the `price` field from `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol  line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

No check is made on `answeredInRound >= roundId` (round completeness), `updatedAt != 0` (round started), `updatedAt` within a heartbeat window (staleness), or `price > 0` (valid answer). By contrast, the pool-level wrapper `ChainlinkOracleForRSETHPoolCollateral.getRate()` performs all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  lines 30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

**Exploit path — why the price ratio does not cancel:**

`LRTDepositPool.getRsETHAmountToMint()` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`rsETHPrice` is the stored value set by `_updateRsETHPrice()`, which aggregates all supported assets via `_getTotalEthInProtocol()`. [4](#0-3) 

If only one LST feed (e.g. stETH/ETH) goes stale at a price lower than truth, `rsETHPrice` is depressed proportionally to stETH's share of TVL. An attacker who then deposits ETH (priced at 1:1, no Chainlink feed) receives `amount / rsETHPrice_stale` rsETH — more than the true `amount / rsETHPrice_true`. The stale factor does **not** cancel because the deposited asset (ETH) is unaffected by the stale feed while `rsETHPrice` is computed across all assets including the stale one.

Concrete example:
- Pool: 100 ETH + 100 stETH (true price 1.05 ETH). True TVL = 205 ETH, rsETH supply = 200, true `rsETHPrice` = 1.025 ETH.
- stETH/ETH feed goes stale at 1.00 ETH. Stale TVL = 200 ETH, `rsETHPrice_stale` = 1.00 ETH.
- Attacker calls `updateRSETHPrice()` (public, no access control) to write the stale price.
- Attacker deposits 10 ETH → mints `10 / 1.00 = 10 rsETH` instead of the correct `10 / 1.025 ≈ 9.756 rsETH`.
- Feed recovers; attacker holds ~0.244 excess rsETH backed by other holders' value.

**Why existing guards are insufficient:**

`_updateRsETHPrice()` contains a downside-protection check: [5](#0-4) 

This only triggers if `pricePercentageLimit > 0` AND the price drop exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`. Two failure modes remain:
1. `pricePercentageLimit` is `0` by default (not set in `initialize()`), disabling all downside protection entirely.
2. Even when set, staleness within the configured limit (e.g. a 0.5% stale deviation with a 1% limit) passes through undetected, still allowing profitable dilution.

`updateRSETHPrice()` is unrestricted: [6](#0-5) 

Any EOA can call it permissionlessly to commit the stale price to storage before depositing.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders accumulate yield as the protocol's TVL grows relative to rsETH supply. When an attacker mints rsETH at a depressed `rsETHPrice`, the subsequent price recovery dilutes all existing holders: the attacker's excess rsETH is redeemable for more ETH than was deposited, and that surplus is drawn directly from the yield owed to prior holders. The magnitude scales with the stale price deviation and the attacker's deposit size; it is a direct, quantifiable extraction of accrued yield.

## Likelihood Explanation

**Medium.** Chainlink LST/ETH feeds have heartbeat intervals (commonly 1 hour) and deviation thresholds. Staleness windows occur during Ethereum network congestion, Chainlink node outages, and feed migration periods. The attack requires no special role, no governance capture, and no private key compromise — only monitoring for feed staleness and calling two public functions (`updateRSETHPrice()` then `depositAsset()`/`depositETH()`). It is repeatable whenever a feed goes stale.

## Recommendation

Add staleness and validity guards to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be configured per-asset based on the Chainlink feed's documented heartbeat interval (e.g. 3600 seconds for a 1-hour heartbeat feed, with a small buffer).

## Proof of Concept

**Foundry fork test outline:**

```solidity
// Fork mainnet at a block where stETH/ETH feed is current.
// 1. Record true rsETHPrice via lrtOracle.rsETHPrice().
// 2. Warp block.timestamp forward by > feed heartbeat (e.g. +2 hours) without
//    advancing the Chainlink round (simulate staleness by mocking latestRoundData
//    to return an old updatedAt).
// 3. Call lrtOracle.updateRSETHPrice() as an unprivileged EOA.
// 4. Assert lrtOracle.rsETHPrice() < pre-warp value (stale price accepted).
// 5. Deposit ETH via lrtDepositPool.depositETH{value: 10 ether}(0, "").
// 6. Assert rsETH minted > 10 ether * 1e18 / trueRsETHPrice (excess minted).
// 7. Warp back to current time; call updateRSETHPrice() again with live feed.
// 8. Assert rsETHPrice recovers; attacker's rsETH balance is worth more ETH
//    than deposited, at the expense of pre-existing holders' share value.
```

The test can be run as a differential: compare `rsethMinted_stale` vs `rsethMinted_live` for the same ETH deposit amount. Any positive difference is the attacker's profit extracted from existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
