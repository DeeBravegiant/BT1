Audit Report

## Title
Missing Staleness and Completeness Validation in `ChainlinkPriceOracle.getAssetPrice()` Enables Excess rsETH Minting — (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `answeredInRound`, and `roundId`, accepting any stale or incomplete price without validation. A stale inflated price propagates directly into `LRTDepositPool.getRsETHAmountToMint()`, causing depositors to receive more rsETH than their deposit is worth and permanently diluting the yield of existing rsETH holders. The same codebase already implements the correct checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming the protocol is aware of the requirement.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` at L52 reads only the `price` field from `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The discarded fields are `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`. No check is made that:
- `updatedAt != 0` (round is complete)
- `answeredInRound >= roundId` (answer is from the current round, not a prior stale round)
- `block.timestamp - updatedAt <= heartbeat` (price is not older than the feed's update window)
- `price > 0` (price is positive)

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` at L27–32 performs all of these checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price returned by `ChainlinkPriceOracle` is consumed in two critical paths:

1. **Direct minting**: `LRTDepositPool.getRsETHAmountToMint()` at L520 computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. An inflated stale `getAssetPrice` numerator directly increases the rsETH minted per unit of deposited asset.

2. **rsETH price update**: `LRTOracle._getTotalEthInProtocol()` at L339 uses `getAssetPrice(asset)` to compute total ETH in protocol, which feeds `_updateRsETHPrice()`. A stale inflated price inflates the computed TVL and thus the rsETH price, compounding the minting error.

Existing guards are insufficient: `_beforeDeposit` only checks deposit limits and a `minRSETHAmountExpected` slippage parameter set by the attacker themselves. There is no on-chain check that the oracle price is fresh before minting.

## Impact Explanation

**High — Theft of unclaimed yield.**

When a stale inflated price is accepted:
- `getAssetPrice(asset)` returns a value above the true market rate
- `getRsETHAmountToMint` mints excess rsETH to the depositor
- The excess rsETH is unbacked by real assets; its value is extracted from the pool backing all existing rsETH

Every existing rsETH holder's proportional claim on the underlying TVL is permanently reduced by the over-minted supply. This constitutes theft of yield accrued by existing stakers, matching the allowed impact class "Theft of unclaimed yield."

## Likelihood Explanation

**Medium.** Chainlink feeds operate on a heartbeat (e.g., 1 hour for stETH/ETH on mainnet) and a deviation threshold. During low-volatility periods, the feed may not update for the full heartbeat window. If the true price drops within that window, the stale higher price remains on-chain. No privileged access is required. Any depositor can read the on-chain `updatedAt` timestamp from the Chainlink feed, confirm the price is stale and above the true rate, and call `depositAsset` to exploit the discrepancy. The attack is repeatable every time the feed is stale and the true price is below the last reported price.

## Recommendation

Add staleness and completeness checks in `ChainlinkPriceOracle.getAssetPrice()`, consistent with `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
require(price > 0, "Invalid price");
```

`MAX_STALENESS` should be configured per asset based on the Chainlink heartbeat for that feed (e.g., 3600 seconds for a 1-hour heartbeat feed, with a small buffer).

## Proof of Concept

**Setup (fork test against mainnet):**

1. Deploy a mock `AggregatorV3Interface` that returns a fixed stale price with `updatedAt = block.timestamp - 7200` (2 hours ago, past the 1-hour heartbeat) and `answeredInRound < roundId`.
2. Register this mock as the price feed for stETH in `ChainlinkPriceOracle` via `updatePriceFeedFor`.
3. Set mock price to `1.06e18` (stale high price); true market price is `1.04e18`.
4. Set `rsETHPrice` to `1.05e18` (last valid update).

**Attack sequence:**

```
attacker.call(LRTDepositPool.depositAsset(stETH, 1e18, 0, ""))
  → _beforeDeposit → getRsETHAmountToMint(stETH, 1e18)
    → lrtOracle.getAssetPrice(stETH)
      → ChainlinkPriceOracle.getAssetPrice(stETH)
        → latestRoundData() returns stale price 1.06e18  ← no staleness check
      → returns 1.06e18
    → rsethAmountToMint = 1e18 * 1.06e18 / 1.05e18 ≈ 1.00952e18
  → mint 1.00952e18 rsETH to attacker
```

**Expected result:** Attacker receives `~1.00952 rsETH` for `1 stETH` worth `1.04 ETH` at true price. The `~0.00952 rsETH` excess is unbacked and dilutes all existing rsETH holders.

**Foundry test plan:**

```solidity
function testStaleOracleMintExcess() public {
    // fork mainnet, deploy mock stale Chainlink feed
    MockStaleFeed feed = new MockStaleFeed(1.06e8, block.timestamp - 7200, roundId - 1);
    vm.prank(lrtManager);
    chainlinkOracle.updatePriceFeedFor(stETH, address(feed));

    uint256 balanceBefore = rsETH.balanceOf(attacker);
    vm.prank(attacker);
    depositPool.depositAsset(stETH, 1e18, 0, "");
    uint256 minted = rsETH.balanceOf(attacker) - balanceBefore;

    // minted > fair share (1e18 * 1.04e18 / 1.05e18 ≈ 0.99047e18)
    assertGt(minted, 0.99047e18);
}
```