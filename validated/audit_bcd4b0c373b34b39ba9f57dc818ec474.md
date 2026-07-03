Audit Report

## Title
Missing Staleness Check on Chainlink `latestRoundData` Enables Phantom Fee Minting on Stale Prices — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` silently discards the `updatedAt` return value from `latestRoundData()`, accepting arbitrarily stale prices with no freshness validation. When a Chainlink feed freezes at an inflated price, any caller can invoke the permissionless `updateRSETHPrice()` to trigger fee minting to the treasury for yield that never occurred, directly diluting rsETH holders' unclaimed yield.

## Finding Description

**Root cause:** `ChainlinkPriceOracle.getAssetPrice` fetches price but discards all staleness indicators:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Neither `updatedAt` nor `answeredInRound` is validated. This stale price flows through `LRTOracle.getAssetPrice` → `_getTotalEthInProtocol`: [2](#0-1) 

`_updateRsETHPrice` then computes `previousTVL = rsethSupply × rsETHPrice` and mints protocol fees whenever `totalETHInProtocol > previousTVL`: [3](#0-2) 

`updateRSETHPrice()` is a permissionless `public` function callable by any address: [4](#0-3) 

**Exploit path:**
1. **T0:** Feed at P0; `updateRSETHPrice()` called; `rsETHPrice` anchored at P0-based value; `highestRsethPrice` set.
2. **T0→T1:** Feed legitimately updates to P1 > P0 (genuine yield accrual), but `updateRSETHPrice()` is not called.
3. **T1:** Actual LST price drops back toward P0 (slashing, market correction). Chainlink feed **stops updating** (heartbeat miss, L2 sequencer down) and remains frozen at P1.
4. **T2:** Attacker calls `updateRSETHPrice()`. `_getTotalEthInProtocol()` uses stale P1 (inflated). `previousTVL` is anchored at P0-based rsETHPrice. Since P1 > P0, `totalETHInProtocol > previousTVL`, and protocol fee is minted to treasury for the phantom P0→P1 gain even though the actual current price is below P1.

**Why existing guards fail:**

The `pricePercentageLimit` guard only reverts non-manager callers if `newRsETHPrice > highestRsethPrice` AND the increase exceeds the configured threshold: [5](#0-4) 

This guard is bypassed when: (a) `pricePercentageLimit == 0` (disabled by default until explicitly set), (b) the stale price delta is within the configured limit, or (c) `highestRsethPrice` was already updated to P1 in a prior call. The fee computation at L244–247 runs unconditionally before this check, and the `maxFeeMintAmountPerDay` cap only bounds per-day damage without preventing the exploit: [6](#0-5) 

## Impact Explanation

**High — Theft of unclaimed yield.** The rsETH minted to the treasury is backed by phantom TVL growth. Existing rsETH holders receive less ETH per rsETH than they are entitled to: the delta `(stalePriceP1 − actualPriceP2) × totalDeposits × feeRate / newRsETHPrice` in rsETH is transferred from holders to the treasury. This is a direct, quantifiable transfer of unclaimed yield from rsETH holders to the treasury, matching the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation

- Chainlink heartbeat misses are a documented operational risk; L2 sequencer downtime (Arbitrum, Base, etc.) is a known recurring event that causes feed staleness.
- `updateRSETHPrice()` is permissionless — any EOA or contract can call it at the worst moment.
- No oracle freshness validation exists anywhere in the call chain from `updateRSETHPrice()` through `_getTotalEthInProtocol()` to `ChainlinkPriceOracle.getAssetPrice`.
- The attack requires no capital, no special role, and is repeatable each time a feed goes stale.

## Recommendation

In `ChainlinkPriceOracle.getAssetPrice`, validate `updatedAt` against a configurable per-asset maximum staleness:

```solidity
(, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
if (block.timestamp - updatedAt > maxStaleness[asset]) revert StalePrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Store a per-asset `maxStaleness` mapping set by the LRT manager, aligned with each feed's published heartbeat interval (e.g., 3600s for a 1-hour heartbeat feed). Additionally, consider checking `answeredInRound >= roundId` to guard against incomplete rounds.

## Proof of Concept

```solidity
// Foundry fork test (local fork, no public-mainnet calls)
function test_stalePriceCausesPhantomFeeMint() external {
    // 1. Deploy mock Chainlink feed returning P0 with fresh updatedAt
    MockAggregator feed = new MockAggregator(P0, block.timestamp);
    chainlinkOracle.updatePriceFeedFor(asset, address(feed));

    // 2. Anchor rsETHPrice at P0
    lrtOracle.updateRSETHPrice();
    uint256 rsETHPriceBefore = lrtOracle.rsETHPrice();

    // 3. Simulate: feed updated to P1 > P0, then froze 2 days ago
    feed.setPrice(P1);
    feed.setUpdatedAt(block.timestamp - 2 days);
    vm.warp(block.timestamp + 2 days); // advance time; actual price is back at P0

    // 4. Any unprivileged caller triggers updateRSETHPrice — no staleness revert
    vm.prank(attacker);
    lrtOracle.updateRSETHPrice(); // succeeds; fee minted for phantom P0→P1 gain

    // 5. Assert fee was minted to treasury (rsETH supply increased, price diluted)
    assertGt(IRSETH(rsETH).balanceOf(treasury), 0);
    assertLt(lrtOracle.rsETHPrice(), rsETHPriceBefore); // holders diluted
}
```

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L234-247)
```text
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
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTOracle.sol (L299-308)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
