All code references check out. Let me verify the exact line numbers and confirm the full exploit path before rendering the final output.

The code confirms every claim:

- `ChainlinkPriceOracle.getAssetPrice()` at L52 discards all return values except `price` [1](#0-0) 
- `updateRSETHPrice()` at L87 is `public` with only `whenNotPaused` — callable by anyone [2](#0-1) 
- The downside-protection pause at L277-281 fires when `diff > pricePercentageLimit.mulWad(highestRsethPrice)` [3](#0-2) 
- `ChainlinkOracleForRSETHPoolCollateral.getRate()` at L30-32 performs `answeredInRound < roundID` and `timestamp == 0` checks, confirming the team knows the pattern [4](#0-3) 
- SECURITY.md L20 excludes "Incorrect data supplied by third-party oracles" but L26 carves back in oracle manipulation attacks; the bug here is a protocol-level missing validation, not oracle operator compromise [5](#0-4) 

---

Audit Report

## Title
Missing Staleness Check on Chainlink Price Feed Allows Stale Prices to Trigger Protocol Pause - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, accepting arbitrarily stale prices with no freshness validation. A stale (lower) LST price propagates into `LRTOracle._updateRsETHPrice()`, which computes a deflated `newRsETHPrice`. If the deflation exceeds `pricePercentageLimit`, the protocol's own downside-protection logic pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`, temporarily freezing all user deposits and withdrawals.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` L52, `getAssetPrice()` calls `latestRoundData()` and silently discards all fields except `answer`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt, answeredInRound ignored
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no `require(block.timestamp - updatedAt <= heartbeat)` guard and no `require(answeredInRound >= roundId)` guard.

This oracle is the sole price source consumed by `LRTOracle.getAssetPrice()` (L156-158), which is called inside `_getTotalEthInProtocol()` (L339-343) to compute `totalETHInProtocol`. `_updateRsETHPrice()` then derives `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` (L250).

The downside-protection branch at L270-281 fires when:
```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
```

LST prices in ETH terms are monotonically non-decreasing (they accrue staking rewards). A stale feed returns an older, lower price. If the gap between the stale price and `highestRsethPrice` exceeds `pricePercentageLimit`, the protocol auto-pauses.

`updateRSETHPrice()` (L87) is `public` with only a `whenNotPaused` modifier — any unprivileged caller can trigger this path.

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` L30-32 performs `answeredInRound < roundID` and `timestamp == 0` checks on the same `latestRoundData()` interface, confirming the team is aware of the staleness-check pattern but did not apply it to `ChainlinkPriceOracle`.

## Impact Explanation
**Medium — Temporary freezing of funds.**

A stale Chainlink price for any supported LST asset causes `_updateRsETHPrice()` to compute a deflated `newRsETHPrice`. If the deflation exceeds `pricePercentageLimit` (e.g., 1% = `1e16`), the protocol's downside-protection logic pauses `LRTDepositPool` and `LRTWithdrawalManager`. Users cannot deposit collateral or initiate withdrawals until an admin with `DEFAULT_ADMIN_ROLE` calls `unpause()` on each contract. Funds are not lost but are inaccessible for the duration of the pause.

## Likelihood Explanation
Chainlink LST/ETH feeds operate on a 24-hour heartbeat with a deviation threshold. During Ethereum network congestion or oracle infrastructure incidents, a feed can miss its heartbeat update. Because LST prices move slowly (~4% APY), the deviation threshold is rarely breached, making heartbeat-only updates the primary update mechanism and thus the primary staleness risk. No attacker action is required: any caller — including an automated keeper or a regular user — triggers the stale-price path by calling the public `updateRSETHPrice()` whenever the feed has not been updated within its expected heartbeat. The condition is passively reachable without any privileged access or external coordination.

## Recommendation
Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price: round not complete");
require(updatedAt != 0, "Stale price: incomplete round");
require(block.timestamp - updatedAt <= MAX_STALENESS_SECONDS, "Stale price: too old");
require(price > 0, "Invalid price");
```

`MAX_STALENESS_SECONDS` should be set per feed based on its documented heartbeat (e.g., 86 400 s for 24 h feeds, 3 600 s for 1 h feeds), mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`.

## Proof of Concept
1. A supported LST asset (e.g., stETH) has its Chainlink feed go stale — the feed's last update was 25 hours ago (heartbeat = 24 h), but the price has not moved enough to trigger a deviation update.
2. Any unprivileged user calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the 25-hour-old price — lower than the true current price by, say, 1.5% (accumulated staking yield since last update).
4. `newRsETHPrice` is computed using this deflated asset valuation and falls below `highestRsethPrice` by more than `pricePercentageLimit` (e.g., 1% = `1e16`).
5. `_updateRsETHPrice()` executes the downside-protection branch: `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`.
6. All user deposits and withdrawals are frozen. The protocol remains paused until an admin calls `unpause()` on each contract.

**Foundry fork test plan:** Fork mainnet, set `pricePercentageLimit` to `1e16` (1%), `vm.warp` forward by 25 hours without triggering a Chainlink feed update (use `vm.mockCall` to return a `latestRoundData` response with `updatedAt = block.timestamp - 25 hours`), call `updateRSETHPrice()` from an unprivileged address, and assert that `lrtDepositPool.paused()`, `withdrawalManager.paused()`, and `lrtOracle.paused()` all return `true`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-52)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L277-281)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** SECURITY.md (L20-26)
```markdown
- Incorrect data supplied by third-party oracles.
- Impacts requiring basic economic and governance attacks (e.g. 51% attack).
- Lack of liquidity impacts.
- Impacts from Sybil attacks.
- Impacts involving centralization risks.

Note: This does not exclude oracle manipulation/flash-loan attacks.
```
