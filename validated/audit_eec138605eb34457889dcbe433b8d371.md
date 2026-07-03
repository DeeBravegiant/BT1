Audit Report

## Title
Missing Staleness and Validity Checks in `ChainlinkPriceOracle.getAssetPrice()` Enables Stale-Price rsETH Over-Minting and Protocol Auto-Pause - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `answeredInRound`, `roundId`, and performs no `price > 0` check. The raw answer is returned unconditionally. This stale or invalid price feeds two critical paths: rsETH over-minting at a pre-depeg rate (diluting existing holders — High: Theft of unclaimed yield) and artificial underestimation of `totalETHInProtocol` that can trigger the auto-pause circuit breaker (Medium: Temporary freezing of funds). The contrast with `ChainlinkOracleForRSETHPoolCollateral`, which performs all three checks in the same repository, confirms the omission is unintentional.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the `answer` field:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is made on:
- `updatedAt` — whether the price was refreshed within the oracle heartbeat window
- `answeredInRound >= roundId` — whether the round is complete
- `price > 0` — whether the answer is valid (a negative `int256` cast to `uint256` in Solidity 0.8 wraps to a near-`type(uint256).max` value rather than reverting)

`ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository performs all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

**Path 1 — rsETH over-minting (High):**

`LRTDepositPool.getRsETHAmountToMint()` computes:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` delegates directly to `ChainlinkPriceOracle.getAssetPrice()`. If the Chainlink feed for a supported LST is stale (e.g., stETH depegs from 1.0 ETH to 0.8 ETH but the feed has not yet updated past its deviation threshold), the stale high price is used. A depositor who deposits the depegged LST receives rsETH calculated at the pre-depeg rate, extracting value from all existing rsETH holders.

**Path 2 — Auto-pause via artificially low rsETH price (Medium):**

`LRTOracle._getTotalEthInProtocol()` sums:

```solidity
// contracts/LRTOracle.sol L339-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

A stale or zero price for any single supported asset underestimates `totalETHInProtocol`, producing an artificially low `newRsETHPrice`. If the computed drop from `highestRsethPrice` exceeds `pricePercentageLimit`, the downside-protection branch executes:

```solidity
// contracts/LRTOracle.sol L277-281
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

`updateRSETHPrice()` is publicly callable (`public whenNotPaused`), so any external actor can trigger this path at any time the oracle is stale.

## Impact Explanation

**Path 1 — High: Theft of unclaimed yield.** Existing rsETH holders have accumulated yield reflected in the rising rsETH price. A depositor who mints rsETH at a stale pre-depeg rate receives a disproportionate share of the underlying ETH pool, directly diluting the accumulated yield of all existing holders. The excess rsETH minted represents a concrete, quantifiable transfer of value (e.g., ~25% over-issuance for a 20% depeg).

**Path 2 — Medium: Temporary freezing of funds.** A stale oracle for any supported LST causes `_updateRsETHPrice()` to compute an artificially low rsETH price. If the apparent drop exceeds `pricePercentageLimit`, all deposits and withdrawals are frozen until an admin manually unpauses. This is a griefing-capable, publicly triggerable freeze.

## Likelihood Explanation

Chainlink LST/ETH feeds operate on heartbeat (typically 24 hours) and deviation threshold (typically 0.5%). During a depeg event where the price moves within the deviation band, the feed will not update for up to 24 hours — a well-documented real-world scenario (stETH depegged in May 2022). For Path 2, any period of oracle inactivity combined with a previously high `highestRsethPrice` is sufficient. Both paths are triggerable by an unprivileged external caller with no special access: Path 1 via `depositAsset()`, Path 2 via `updateRSETHPrice()`.

## Recommendation

Add staleness, round-completeness, and positivity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally: if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept

**Path 1 (Theft of unclaimed yield):**

1. stETH is a supported LST with a Chainlink stETH/ETH feed (24h heartbeat, 0.5% deviation).
2. stETH depegs: real price drops from 1.0 ETH to 0.8 ETH, but the move is within the deviation band so the feed has not updated. Feed still reports `price = 1e8` (stale).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
4. `getRsETHAmountToMint` computes `rsethAmountToMint = (100e18 * 1e18) / rsETHPrice` using the stale 1.0 ETH price instead of the real 0.8 ETH price.
5. Attacker receives ~25% more rsETH than the deposited stETH is worth in ETH terms, diluting all existing holders' accumulated yield.

**Path 2 (Temporary freezing of funds):**

1. Any supported LST Chainlink feed becomes stale and returns a price significantly below the last recorded `highestRsethPrice` (e.g., feed returns last-known price from before a period of LST appreciation).
2. Any external caller invokes `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` underestimates TVL using the stale price; `newRsETHPrice` falls below `highestRsethPrice` by more than `pricePercentageLimit`.
4. The `isPriceDecreaseOffLimit` branch fires, pausing `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`.
5. All user deposits and withdrawals are frozen until an admin manually unpauses.

**Foundry fork test outline:**

```solidity
function test_staleOracleOverMint() public {
    // Fork mainnet, mock stETH Chainlink feed to return stale pre-depeg price
    vm.mockCall(stETHFeed, abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
        abi.encode(1, 1e8, 0, block.timestamp - 25 hours, 0)); // stale, answeredInRound < roundId
    uint256 rsethBefore = rsETH.balanceOf(attacker);
    vm.prank(attacker);
    depositPool.depositAsset(stETH, 100e18, 0, "");
    uint256 rsethMinted = rsETH.balanceOf(attacker) - rsethBefore;
    // Assert rsethMinted > fair value based on real price
    assertGt(rsethMinted, fairRsethAmount);
}
```