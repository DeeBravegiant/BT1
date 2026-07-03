Audit Report

## Title
`RSETHPriceFeed.latestRoundData()` Returns `updatedAt` Solely from the ETH/USD Feed, Masking a Stale rsETH/ETH Rate - (File: `contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed.latestRoundData()` computes an rsETH/USD price by multiplying the ETH/USD Chainlink price by `LRTOracle.rsETHPrice()`, but the returned `updatedAt` timestamp is taken exclusively from the ETH/USD feed. `LRTOracle` exposes no `lastUpdated` field and `updateRSETHPrice()` is a public, non-automatic function gated by `whenNotPaused`. If the rsETH/ETH rate goes stale while the ETH/USD feed remains active, downstream consumers that validate `updatedAt` will pass their freshness check against a price that embeds a stale rsETH/ETH component.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol` lines 63–70:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

`updatedAt` is set from `ETH_TO_USD.latestRoundData()` only. The rsETH/ETH component comes from `RS_ETH_ORACLE.rsETHPrice()`, which reads the `rsETHPrice` storage variable in `LRTOracle`. That variable is only updated when `updateRSETHPrice()` (line 87 of `LRTOracle.sol`) is called externally — it is not called automatically. `LRTOracle` stores no `lastUpdated` timestamp and `ILRTOracle` exposes none (`rsETHPrice()` is the only price-related getter). The same flaw exists in `getRoundData()` (lines 53–61).

The `whenNotPaused` modifier on `updateRSETHPrice()` means that while `LRTOracle` is paused (callable by any `PAUSER_ROLE` holder per line 138), the rsETH/ETH rate freezes while the ETH/USD Chainlink feed continues updating on its own heartbeat. Even without a pause, a keeper outage produces the same divergence. There are no existing guards in `RSETHPriceFeed` that check the age of `rsETHPrice`.

## Impact Explanation
**Critical — Direct theft of user funds.** If `rsETHPrice` is stale-high (rsETH has depreciated but `updateRSETHPrice()` was not called), `latestRoundData()` returns an inflated rsETH/USD price with a fresh-looking `updatedAt`. A borrower on Morpho (or any lending protocol consuming this feed) can draw more debt than their actual collateral supports. Lenders bear the resulting bad debt — this is direct theft of lender funds, matching the Critical impact class. The stale-low direction (rsETH appreciated, price not updated) causes healthy positions to appear undercollateralized and be liquidated, constituting temporary freezing of user funds (Medium), but the stale-high direction reaches Critical.

## Likelihood Explanation
`updateRSETHPrice()` depends entirely on off-chain keepers or manual calls; no on-chain mechanism enforces it. Keeper failures (bugs, network congestion, missed windows) are a well-documented operational risk. Additionally, the protocol's own `pause()` function — callable by any address holding `PAUSER_ROLE` — directly and deterministically freezes `rsETHPrice` while the ETH/USD feed continues. No attacker capability beyond holding rsETH and a Morpho borrow position is required to exploit the stale-high scenario once the divergence exists. Likelihood is Medium.

## Recommendation
`latestRoundData()` should return `updatedAt` as the minimum of the ETH/USD feed's `updatedAt` and the rsETH oracle's last-update timestamp. `LRTOracle` must expose a `lastUpdated` storage variable (set to `block.timestamp` inside `_updateRsETHPrice()` at line 313 of `LRTOracle.sol`) and surface it via `ILRTOracle`. `RSETHPriceFeed` should then compute:

```solidity
uint256 rsETHLastUpdated = RS_ETH_ORACLE.lastUpdated();
updatedAt = updatedAt < rsETHLastUpdated ? updatedAt : rsETHLastUpdated;
```

This ensures any consumer's staleness check reflects the freshness of both price components.

## Proof of Concept
1. Deploy a local fork. Call `LRTOracle.updateRSETHPrice()` to set a baseline `rsETHPrice`.
2. Advance time by 6 hours (`vm.warp`) without calling `updateRSETHPrice()` again (simulating keeper failure). The ETH/USD Chainlink mock is updated normally.
3. During this window, simulate rsETH depreciation by reducing underlying asset values (e.g., reduce staked asset balances in the deposit pool mock) so the true rsETH/ETH rate has fallen, but `rsETHPrice` still holds the old higher value.
4. Call `RSETHPriceFeed.latestRoundData()`. Assert: `updatedAt` is within the last hour (ETH/USD heartbeat), passing any `maxAge = 1 hour` check. Assert: `answer` is inflated relative to the true price.
5. From a borrower account, call Morpho's `borrow()` using rsETH as collateral at the inflated price. Assert: the borrowed amount exceeds what the true collateral value supports.
6. Advance time further, call `updateRSETHPrice()` to refresh the price. Assert: the position is now undercollateralized, confirming lender funds were extracted against a stale price.