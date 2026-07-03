Audit Report

## Title
Missing Staleness Checks in `getAssetPrice()` Allows Stale Chainlink Price to Corrupt rsETH Pricing - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `roundId`, `updatedAt`, and `answeredInRound`, accepting any price — including one from a stale or incomplete round — without validation. This price feeds directly into `LRTOracle._updateRsETHPrice()`, which is triggerable by any unprivileged caller via the public `updateRSETHPrice()` function, allowing a stale price to corrupt the rsETH exchange rate and cause direct fund mispricing or a spurious protocol-wide pause.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `getAssetPrice()` fetches the Chainlink price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values are available per the interface declared at lines 14–17, but only `answer` is used. There is no check that `updatedAt > 0` (round is complete), `answeredInRound >= roundId` (answer is not from a prior round), or `price > 0` (answer is valid). [2](#0-1) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository performs all three checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [3](#0-2) 

The exploit path is: any caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)` → stale `latestRoundData()` answer accepted without revert → `totalETHInProtocol` is computed from the stale price → `newRsETHPrice` is set to an incorrect value. [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

## Impact Explanation
Two concrete harm paths exist:

1. **Critical — Direct theft / protocol insolvency**: If the stale price is artificially high (e.g., Chainlink heartbeat missed during a market crash), `totalETHInProtocol` is overstated, `rsETHPrice` is set above actual backing, new depositors receive fewer rsETH than owed (direct theft of depositor value), and existing rsETH becomes undercollateralized (protocol insolvency).

2. **Medium — Temporary freezing of funds**: If the stale price is artificially low (e.g., an incomplete round with `updatedAt == 0` returning a near-zero answer), `newRsETHPrice` drops below `highestRsethPrice` by more than `pricePercentageLimit`, triggering the auto-pause that freezes all deposits and withdrawals. [8](#0-7) 

## Likelihood Explanation
`updateRSETHPrice()` is public and permissionless — any external address can call it at any time, including during a window when a Chainlink feed is stale. Chainlink feeds can return stale data during network congestion, sequencer downtime on L2, or when the heartbeat interval expires without a deviation trigger. No special privileges, victim mistakes, or external protocol compromise are required; the attacker simply calls a public function at the right moment. [4](#0-3) 

## Recommendation
Apply the same staleness checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    require(answeredInRound >= roundId, "Stale price");
    require(updatedAt > 0, "Incomplete round");
    require(price > 0, "Invalid price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Optionally add a per-feed `block.timestamp - updatedAt <= MAX_STALENESS` heartbeat check.

## Proof of Concept
1. Deploy a mock `AggregatorV3Interface` that returns `answeredInRound < roundId` (stale) or `updatedAt == 0` (incomplete round) with an arbitrary `answer`.
2. Configure `ChainlinkPriceOracle` to use this mock feed for a supported LST asset (e.g., stETH).
3. Call `LRTOracle.updateRSETHPrice()` from any EOA.
4. Observe that `getAssetPrice(stETH)` returns the stale/invalid answer without reverting.
5. Observe that `rsETHPrice` is updated to a value derived from the stale price.
6. For the theft path: set mock answer above true market price; verify new depositors receive fewer rsETH than the ETH value they deposited.
7. For the freeze path: set mock answer to 1 (near-zero); verify `isPriceDecreaseOffLimit` triggers and the protocol is paused, freezing all deposits and withdrawals.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L14-17)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);
```

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

**File:** contracts/LRTOracle.sol (L273-281)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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
