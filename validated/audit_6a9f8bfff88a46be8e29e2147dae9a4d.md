Audit Report

## Title
Missing Chainlink Oracle Staleness/Validity Checks Enable Permissionless Protocol-Wide Auto-Pause — (File: `contracts/oracles/ChainlinkPriceOracle.sol`, `contracts/LRTOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` discards all validity fields from `latestRoundData()`, accepting stale or zero prices without revert. Because `LRTOracle._getTotalEthInProtocol()` aggregates every supported asset through this unchecked oracle, a single feed returning 0 collapses the computed TVL, causing `_updateRsETHPrice()` to trigger its downside-protection auto-pause and freeze all deposits and withdrawals. `updateRSETHPrice()` is public, so any unprivileged caller can force this path the moment any feed goes stale.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the `answer` field and silently discards `updatedAt`, `answeredInRound`, and `roundId`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — used for pool collateral in the same codebase — correctly validates all three conditions:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` loops over every supported asset and calls the unchecked oracle for each: [3](#0-2) 

If any single `assetER` is 0, that asset's entire TVL contribution is erased from `totalETHInProtocol`. `_updateRsETHPrice()` then computes `newRsETHPrice` far below `highestRsethPrice` and executes the auto-pause branch: [4](#0-3) 

`updateRSETHPrice()` carries no access control — any address can call it: [5](#0-4) 

The auto-pause calls `lrtDepositPool.pause()` and `withdrawalManager.pause()`, blocking all user-facing operations until an admin manually unpauses each contract. [6](#0-5) 

## Impact Explanation

When the auto-pause fires, `LRTDepositPool` and `LRTWithdrawalManager` are both paused, blocking `depositETH`, `depositAsset`, and all withdrawal operations for every user. Recovery requires manual admin intervention to unpause three contracts. This is **temporary freezing of funds** (Medium severity) — a concrete, allowed impact in the program scope.

## Likelihood Explanation

Chainlink feeds can legitimately return stale or zero data during heartbeat misses under network congestion, feed deprecation/migration, circuit-breaker events, or L2 sequencer downtime. The protocol supports multiple LST assets (stETH, ETHx, etc.), each with its own feed; the probability that at least one feed experiences a transient anomaly over the protocol's lifetime is non-trivial. No privileged access is required: `updateRSETHPrice()` is public, so any observer — including a keeper bot or a user who notices a stale feed — can trigger the pause immediately.

## Recommendation

1. Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt > 0, "Incomplete round");
require(price > 0, "Invalid price");
```

2. In `_getTotalEthInProtocol()`, consider skipping or using a cached price for assets whose oracle reverts or returns an invalid value, rather than allowing a single bad feed to corrupt the aggregate TVL.

3. Decouple the auto-pause trigger from oracle-computed TVL so that a transient feed anomaly does not automatically freeze user funds without additional circuit-breaker logic or a time-delay.

## Proof of Concept

1. Protocol has two supported assets: ETH and stETH. `pricePercentageLimit` is set to `1e16` (1%). `highestRsethPrice` is `1.05e18`.
2. The stETH Chainlink feed experiences a heartbeat miss; `latestRoundData()` returns `answeredInRound < roundId` with `price = 0`.
3. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0` — no revert, no check.
4. `_getTotalEthInProtocol()` computes `totalETHInProtocol` as if all stETH TVL is worth 0 ETH.
5. `newRsETHPrice = totalETHInProtocol / rsethSupply` computes to, say, `0.5e18`.
6. `diff = 1.05e18 - 0.5e18 = 0.55e18 > pricePercentageLimit.mulWad(highestRsethPrice) = 0.0105e18` → `isPriceDecreaseOffLimit = true`.
7. Any address calls `updateRSETHPrice()`.
8. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()` execute.
9. All deposits and withdrawals revert with `Pausable: paused` until admin manually unpauses — **temporary protocol-wide freeze**.

**Foundry fork test outline:**
```solidity
function test_staleFeedTriggersAutoPause() public {
    // 1. Deploy mock Chainlink feed that returns price=0 for stETH
    MockStaleFeed staleFeed = new MockStaleFeed(); // returns (1, 0, 0, 0, 0)
    vm.prank(lrtManager);
    chainlinkOracle.updatePriceFeedFor(stETH, address(staleFeed));

    // 2. Any unprivileged caller triggers the update
    vm.prank(address(0xdead));
    lrtOracle.updateRSETHPrice();

    // 3. Assert all three contracts are paused
    assertTrue(lrtDepositPool.paused());
    assertTrue(withdrawalManager.paused());
    assertTrue(lrtOracle.paused());
}
```

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
