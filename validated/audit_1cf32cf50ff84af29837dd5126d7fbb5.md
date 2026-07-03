Audit Report

## Title
Unvalidated Chainlink `latestRoundData()` Return in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale/Zero Price to Deflate `rsETHPrice` and Dilute Existing Holders - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` discards all validity fields from `latestRoundData()`, silently accepting a zero price during a stale Chainlink round. This zero propagates through `LRTOracle._getTotalEthInProtocol()`, deflating the computed TVL and writing an artificially low `rsETHPrice` to storage. Any subsequent depositor then receives excess rsETH at the deflated rate, directly diluting existing rsETH holders. The same codebase already applies the correct validation pattern in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming developer awareness of the requirement.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the `answer` field and performs no validation:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Three checks are absent: `answeredInRound >= roundId`, `updatedAt != 0`, and `price > 0`. By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` applies all three:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol:30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle wired into `LRTOracle` for L1 LST assets. `_getTotalEthInProtocol()` calls `getAssetPrice(asset)` for each supported asset: [3](#0-2) 

A zero `assetER` silently zeros out that asset's entire TVL contribution. The resulting `newRsETHPrice` is deflated: [4](#0-3) 

The protocol has a downside-protection mechanism at lines 270–282 that pauses the protocol if the price drop exceeds `pricePercentageLimit`. However, this guard is gated on `pricePercentageLimit > 0`: [5](#0-4) 

`pricePercentageLimit` is a `uint256` storage variable with no initialization in `initialize()`, so its default value is `0`. When `pricePercentageLimit == 0`, `isPriceDecreaseOffLimit` is always `false`, the pause is never triggered, and the deflated price is written unconditionally to `rsETHPrice` at line 313: [6](#0-5) 

`updateRSETHPrice()` is a permissionless `public` function: [7](#0-6) 

`LRTDepositPool.getRsETHAmountToMint()` uses the stored `rsETHPrice` as the denominator: [8](#0-7) 

A deflated denominator mints excess rsETH per deposited unit, diluting all existing holders.

Even when `pricePercentageLimit` is configured, if the affected asset represents a small fraction of TVL, the price drop may fall within the allowed band, the pause is not triggered, and the deflated price is still written — enabling a smaller but still profitable attack.

## Impact Explanation

**Critical — Direct theft of user funds.** Existing rsETH holders' proportional claim on the underlying TVL is diluted when new depositors receive excess rsETH minted against the artificially deflated `rsETHPrice`. The attacker's gain (excess rsETH redeemable for more ETH than deposited once the oracle recovers) is directly extracted from existing holders' share of the pool. This matches the allowed impact: *Critical. Direct theft of any user funds, whether at-rest or in-motion, other than unclaimed yield.*

## Likelihood Explanation

**Medium.** Chainlink feeds enter a state where `answeredInRound < roundId` and `price = 0` during oracle downtime, network congestion, or feed deprecation — these are documented, observable on-chain conditions. The attack requires no privileged access: `updateRSETHPrice()` is permissionless, and `depositAsset()` is open to any user. The attacker only needs to monitor Chainlink round state and sequence two public calls. The attack is repeatable whenever a feed enters a stale round and `pricePercentageLimit` is either unset (0) or the affected asset's TVL share is below the configured threshold.

## Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
```

Additionally, ensure `pricePercentageLimit` is initialized to a non-zero value in `initialize()` so the downside-protection pause is always active as a secondary defense.

## Proof of Concept

**Preconditions:**
- `pricePercentageLimit == 0` (default, uninitialized) OR the stale asset is a small fraction of TVL.
- A Chainlink LST/ETH feed (e.g. stETH/ETH) enters a stale round: `answeredInRound < roundId`, causing `latestRoundData()` to return `price = 0`.

**Attack sequence:**
1. Attacker monitors the stETH/ETH Chainlink feed on-chain and detects `answeredInRound < roundId`.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (permissionless, no role required).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0`.
4. stETH's TVL contribution is zeroed; `totalETHInProtocol` is deflated.
5. `newRsETHPrice = deflatedTVL / rsethSupply` — significantly below true price.
6. With `pricePercentageLimit == 0`, `isPriceDecreaseOffLimit` is `false`; no pause is triggered.
7. `rsETHPrice = newRsETHPrice` is written to storage.
8. Attacker immediately calls `LRTDepositPool.depositAsset(stETH, largeAmount, 0)`.
9. `getRsETHAmountToMint` computes `(largeAmount * stETHPrice) / deflatedRsETHPrice` → mints excess rsETH.
10. When the oracle recovers and `rsETHPrice` is corrected upward, the attacker's excess rsETH is worth more than deposited, at the expense of existing holders.

**Foundry fork test plan:**
- Fork mainnet; deploy/configure contracts with `pricePercentageLimit = 0`.
- Mock the stETH/ETH Chainlink aggregator to return `answeredInRound < roundId` and `price = 0`.
- Call `updateRSETHPrice()` and assert `rsETHPrice` is deflated.
- Call `depositAsset(stETH, amount, 0)` and assert rsETH minted exceeds the fair amount.
- Restore the mock to a valid price, call `updateRSETHPrice()` again, and assert the attacker's rsETH balance represents more ETH value than deposited.

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
