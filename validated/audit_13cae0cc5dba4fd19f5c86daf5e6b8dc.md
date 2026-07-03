Audit Report

## Title
Chainlink Oracle Price Feed Used Without Staleness Check Allows Stale Price to Inflate rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, performing no staleness validation. A stale (frozen) oracle price flows directly into `LRTDepositPool.depositAsset()`, allowing any depositor to mint rsETH at an inflated exchange rate, diluting the share value of all existing rsETH holders and constituting theft of their unclaimed yield.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `getAssetPrice` silently drops all return values except `price`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is made against `block.timestamp - updatedAt` (time-based staleness) or `answeredInRound < roundId` (round-based staleness). This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` lines 30–32, which validates both `answeredInRound < roundID` and `timestamp == 0` and `ethPrice <= 0`.

The stale price propagates through the following confirmed call chain:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` — returns frozen price
2. → `LRTOracle.getAssetPrice(asset)` (L156–158) — passes through to price fetcher
3. → `LRTDepositPool.getRsETHAmountToMint()` (L519–520) — computes `(amount * stalePriceHigh) / rsETHPrice`
4. → `LRTDepositPool._beforeDeposit()` → `depositAsset()` (L111, L99–118) — mints rsETH at inflated rate

The `rsETHPrice` denominator in step 3 is the stored value from the last `updateRSETHPrice()` call, which may have been computed before the staleness window began. The numerator uses the live (stale) oracle call, creating an asymmetry that benefits the depositor.

Additionally, `updateRSETHPrice()` (L87–89) is publicly callable and uses the same stale price in `_getTotalEthInProtocol()` (L339), which can cause incorrect protocol fee minting.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only applies to the `updateRSETHPrice()` path and does not protect the deposit minting path.

## Impact Explanation
**High — Theft of unclaimed yield from existing rsETH holders.**

When a Chainlink feed goes stale (e.g., during the 24-hour heartbeat window for some LST feeds), the last reported price remains frozen. If the real market price of an LST has dropped but the oracle still reports the old higher price, a depositor calling `depositAsset()` receives rsETH calculated at the inflated rate. The depositor receives more rsETH than the deposited assets are worth, permanently diluting the share value of all existing rsETH holders. The yield accrued by existing holders (reflected in a rsETH price above 1e18) is partially transferred to the attacker's inflated rsETH position. This matches the allowed impact: **Theft of unclaimed yield**.

The SECURITY.md exclusion for "Incorrect data supplied by third-party oracles" does not apply here: the oracle is functioning as designed (reporting the last valid price within its heartbeat), and the vulnerability is the contract's failure to consume the `updatedAt` return value that `latestRoundData()` already provides.

## Likelihood Explanation
**Medium.** Chainlink LST/ETH feeds commonly have 24-hour heartbeats with deviation thresholds (e.g., 0.5%). During low-volatility periods or network congestion, feeds can remain at the last reported price for the full heartbeat window while market prices drift. No special permissions are required — any address can call `depositAsset()` or `depositETH()`. The attacker only needs to observe that the on-chain oracle price diverges from the real market price and act within the staleness window. The attack is repeatable across every heartbeat cycle where a price gap exists.

## Recommendation
Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    require(answeredInRound >= roundId, "Stale price: answeredInRound < roundId");
    require(updatedAt != 0, "Incomplete round");
    require(price > 0, "Invalid price");
    require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Stale price: updatedAt too old");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-asset based on the Chainlink feed's documented heartbeat interval, stored in a mapping configurable by the LRT manager.

## Proof of Concept
**Setup (fork test against mainnet or a public testnet):**

1. Deploy or fork with a mock Chainlink aggregator for stETH/ETH that returns `price = 1.05e18`, `updatedAt = block.timestamp - 25 hours`, `answeredInRound = roundId` (simulating a 24h+ stale feed).
2. Set `assetPriceFeed[stETH]` in `ChainlinkPriceOracle` to this mock aggregator.
3. Set `rsETHPrice` in `LRTOracle` to `1.0e18` (baseline, computed before staleness).

**Attack sequence:**

```
// Attacker holds 1000e18 stETH (market value: 950 ETH at 0.95 ETH/stETH)
// Oracle still reports 1.05 ETH/stETH (stale)

uint256 rsethMinted = depositPool.getRsETHAmountToMint(stETH, 1000e18);
// = (1000e18 * 1.05e18) / 1.0e18 = 1050e18 rsETH

depositPool.depositAsset(stETH, 1000e18, 0, "");
// Attacker receives 1050 rsETH, representing 1050 ETH in protocol accounting
// Actual deposited value: 950 ETH at market
```

**Verification:**

- Before attack: existing holders' rsETH price = 1.0e18.
- After attack: `updateRSETHPrice()` is called. `_getTotalEthInProtocol()` uses the same stale oracle, computing TVL as if stETH is worth 1.05 ETH. When the oracle eventually corrects to 0.95 ETH, `_getTotalEthInProtocol()` drops, rsETH price drops, and existing holders' share value is permanently reduced by the 100 ETH discrepancy introduced by the attacker's inflated mint.

**Foundry test plan:**

```solidity
function testStaleOracleInflatedMint() public {
    // 1. Deploy MockAggregator returning price=1.05e18, updatedAt=block.timestamp-25 hours
    // 2. Wire into ChainlinkPriceOracle
    // 3. Snapshot existing holder rsETH balance and rsETHPrice
    // 4. Call depositAsset with 1000e18 stETH
    // 5. Assert rsethMinted > (1000e18 * marketPrice / rsETHPrice)
    // 6. Advance time, call updateRSETHPrice with corrected oracle
    // 7. Assert existing holder's rsETH value decreased (yield stolen)
}
```