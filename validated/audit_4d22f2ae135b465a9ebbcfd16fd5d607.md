Audit Report

## Title
`RSETHPriceFeed` Returns ETH/USD `updatedAt` Instead of Composite Staleness, Masking Stale rsETH Oracle Prices — (File: `contracts/oracles/RSETHPriceFeed.sol`)

## Summary

`RSETHPriceFeed` is a Chainlink-compatible composite feed that multiplies the ETH/USD Chainlink price by the rsETH/ETH rate from `LRTOracle`. Both `latestRoundData()` and `getRoundData()` correctly compute the composite `answer`, but return `updatedAt` exclusively from the ETH/USD Chainlink feed. Because `LRTOracle` stores no timestamp alongside `rsETHPrice` and `IRSETHOracle` exposes no timestamp function, the rsETH/ETH component's staleness is structurally undetectable by any consumer of this feed.

## Finding Description

`RSETHPriceFeed.latestRoundData()` (lines 63–70) and `getRoundData()` (lines 53–61) both follow the same pattern:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
// updatedAt, answeredInRound, roundId remain from ETH_TO_USD only
``` [1](#0-0) 

The `IRSETHOracle` interface exposed to `RSETHPriceFeed` declares only `rsETHPrice()` with no timestamp: [2](#0-1) 

`LRTOracle` stores `rsETHPrice` as a plain `uint256` with no associated timestamp field: [3](#0-2) 

`_updateRsETHPrice()` writes `rsETHPrice = newRsETHPrice` with no timestamp recorded: [4](#0-3) 

`updateRSETHPrice()` is public and permissionless but has no on-chain enforcement of a maximum update interval: [5](#0-4) 

The structural consequence: `RSETHPriceFeed` cannot propagate the rsETH oracle's last-update time into `updatedAt`. The returned `updatedAt` reflects only when the ETH/USD Chainlink round was last updated — which can be very recent even when `rsETHPrice` in `LRTOracle` is hours or days old. Any consumer performing the standard Chainlink staleness check (`require(updatedAt > block.timestamp - maxStaleness)`) will always pass, because the ETH/USD feed updates every few minutes regardless of rsETH oracle freshness.

## Impact Explanation

**Medium — Temporary freezing of funds.**

If `rsETHPrice` in `LRTOracle` becomes stale (keeper delayed or failing) while the ETH/USD Chainlink feed continues updating normally:

- A lending protocol consuming `RSETHPriceFeed` performs its staleness check against `updatedAt` (sourced from ETH/USD) — the check passes.
- If the true rsETH/ETH rate has fallen (e.g., slashing event) but `rsETHPrice` has not been updated, the feed returns an inflated rsETH/USD price. Borrowers can over-borrow against rsETH collateral, leaving the lending protocol under-collateralized.
- If the true rsETH/ETH rate has risen and the oracle is stale, the feed returns a deflated price, triggering incorrect liquidations — temporary freezing of user funds.

The `pricePercentageLimit` downside-protection mechanism in `LRTOracle` only triggers when `updateRSETHPrice()` is actually called; it provides no protection when the function is simply not called. [6](#0-5) 

## Likelihood Explanation

`updateRSETHPrice()` is permissionless but relies entirely on off-chain keepers. There is no on-chain heartbeat enforcement. During market stress — precisely when timely rsETH/ETH updates matter most — keeper delays are most likely. The ETH/USD Chainlink feed will continue updating independently, making the staleness completely invisible to consumers. No privileged access is required to trigger the impact; a normal borrower interacting with any lending protocol that uses this feed as a price oracle is sufficient.

## Recommendation

1. Add a `rsETHPriceLastUpdated` timestamp to `LRTOracle`, written every time `rsETHPrice` is updated.
2. Expose it via `IRSETHOracle` (e.g., `function rsETHPriceLastUpdated() external view returns (uint256)`).
3. In `RSETHPriceFeed`, return `min(ethToUSD_updatedAt, rsETH_lastUpdated)` as `updatedAt`:

```solidity
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
uint256 rsETHUpdatedAt = RS_ETH_ORACLE.rsETHPriceLastUpdated();
if (rsETHUpdatedAt < updatedAt) updatedAt = rsETHUpdatedAt;
```

## Proof of Concept

1. Deploy `LRTOracle` and call `updateRSETHPrice()` at `T=0`; `rsETHPrice` = 1.05e18.
2. Advance time by 25 hours without calling `updateRSETHPrice()`. The ETH/USD Chainlink feed updates normally throughout.
3. Call `RSETHPriceFeed.latestRoundData()`:
   - `answer` = `1.05e18 * ethUSDPrice / 1e18` (stale rsETH/ETH rate)
   - `updatedAt` = current timestamp (from ETH/USD feed, fresh)
4. A lending protocol's staleness check: `require(updatedAt > block.timestamp - 3600)` — passes.
5. A borrower deposits rsETH and borrows at the inflated valuation; the protocol is left under-collateralized.

Foundry fork test outline:
- Fork mainnet, deploy `RSETHPriceFeed` pointing to a mock `LRTOracle` with a fixed `rsETHPrice` and the live ETH/USD Chainlink feed.
- `vm.warp(block.timestamp + 25 hours)` without updating `rsETHPrice`.
- Assert `latestRoundData().updatedAt > block.timestamp - 3600` (passes — demonstrating the staleness is invisible).
- Assert `latestRoundData().answer` still reflects the 25-hour-old rsETH/ETH rate.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L22-24)
```text
interface IRSETHOracle {
    function rsETHPrice() external view returns (uint256);
}
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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

**File:** contracts/LRTOracle.sol (L313-315)
```text
        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```
