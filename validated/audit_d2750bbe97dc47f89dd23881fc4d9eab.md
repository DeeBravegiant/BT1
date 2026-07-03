Audit Report

## Title
Missing Chainlink Staleness Check Enables Block-Stuffing-Assisted Over-Withdrawal via `instantWithdrawal` — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` silently discards the `updatedAt` return value from `latestRoundData()` and applies no heartbeat or staleness guard. An attacker who stuffs blocks to delay Chainlink keeper update transactions can hold a stale-low asset price on-chain, then call `instantWithdrawal` to receive more LST than the burned rsETH is worth at current market rates, draining excess LST from `LRTUnstakingVault`.

## Finding Description

**Root cause — no staleness check:**

In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, only the `answer` field is captured; `updatedAt` is silently discarded:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

No comparison of `updatedAt` against `block.timestamp - heartbeat` is performed anywhere in the function.

**Price propagation path:**

`LRTOracle.getAssetPrice` (line 157) delegates directly to the registered `IPriceFetcher`, passing the potentially stale price through unchanged. `getExpectedAssetAmount` (line 593) then divides by that price:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

If `getAssetPrice(asset)` returns a stale-low value `P_stale < P_real`, the division yields an inflated `underlyingToReceive`.

**Exploit path through `instantWithdrawal`:**

`instantWithdrawal` (lines 228–235) computes `assetAmountUnlocked` from the stale price, burns rsETH, and redeems from the vault. The only guard is a vault-liquidity cap (`CantInstantWithdrawMoreThanAvailable`); there is no price-sanity or staleness check before the burn-and-redeem executes.

**Asymmetry with `unlockQueue`:**

`unlockQueue` (lines 288–295) calls `_validatePrices` with caller-supplied `minimumAssetPrice`/`maximumAssetPrice` bounds before processing. `instantWithdrawal` has no equivalent guard, making it the sole unprotected price-sensitive path.

**Attack sequence:**

1. Attacker monitors the Chainlink feed for an LST whose price is about to rise (e.g., stETH/ETH after a large rebase).
2. Attacker stuffs blocks (fills block gas with high-fee transactions) to prevent Chainlink keeper update transactions from landing, keeping the on-chain answer at the pre-rebase stale-low value.
3. Attacker calls `instantWithdrawal(asset, rsETHUnstaked, ...)` while the stale price persists.
4. `getExpectedAssetAmount` returns an inflated LST amount; rsETH is burned and excess LST is redeemed from `LRTUnstakingVault`.
5. Attacker sells the excess LST at the real market price, profiting the spread.

## Impact Explanation

The invariant `assetAmountUnlocked * realAssetPrice ≤ rsETHUnstaked * rsETHPrice` is violated. `LRTUnstakingVault` loses LST in excess of the rsETH value burned, constituting a direct, quantifiable loss of protocol assets. Impact is **Low — Block stuffing**, matching the allowed scope.

## Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive; the attacker must outbid normal gas prices across consecutive blocks. Profitability requires a large enough price gap and sufficient vault liquidity to cover stuffing costs. This limits realistic exploitation to high-volatility events (large rebases, depeg events) where the spread is wide enough. Likelihood is **Low**, but the code offers zero on-chain resistance once the price is stale.

## Recommendation

Add a staleness check in `ChainlinkPriceOracle.getAssetPrice`:

```solidity
(, int256 price,,uint256 updatedAt,) = priceFeed.latestRoundData();
require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Stale price");
```

`STALENESS_THRESHOLD` should be set per-asset to slightly exceed the feed's documented heartbeat (e.g., 3 600 s for a 1-hour heartbeat feed). Additionally, add price-bounds parameters to `instantWithdrawal` analogous to the `minimumAssetPrice`/`maximumAssetPrice` guards already present in `unlockQueue`.

## Proof of Concept

Fork the mainnet at a block immediately after a stETH rebase but before the Chainlink keeper update lands (simulating block stuffing). Confirm `block.timestamp - updatedAt > heartbeat`. Call `instantWithdrawal(stETH, rsETHAmount, "poc")` and assert that `stethReceived > rsETHAmount * rsETHPrice / realStETHPrice`. The assertion directly proves the invariant violation without any admin compromise or mainnet execution. The PoC in the submission demonstrates this path; note the staleness assertion condition should use `assertGt(block.timestamp - updatedAt, HEARTBEAT)` to correctly confirm the feed is stale before proceeding.