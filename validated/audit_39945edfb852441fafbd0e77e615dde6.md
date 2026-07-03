Audit Report

## Title
Missing Zero-Price Guard and Staleness Check in `ChainlinkPriceOracle.getAssetPrice` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` discards all `latestRoundData()` return values except `answer`, performing no staleness check and no zero-price guard before casting `int256 price` to `uint256`. When Chainlink returns `answer = 0` (circuit-breaker event) and a depositor passes `minRSETHAmountExpected = 0`, the deposit pool transfers the user's LST tokens in but mints 0 rsETH, permanently locking the deposited assets. The staleness path additionally allows deposits to be priced at an arbitrarily outdated rate, causing share mis-accounting for all participants.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice` at lines 49–55:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`updatedAt` and `answeredInRound` are silently discarded. There is no `require(price > 0)` and no heartbeat check.

The deposit call chain is:

1. `LRTDepositPool.depositAsset` → `_beforeDeposit` (line 111) → `getRsETHAmountToMint` (line 665) → `LRTOracle.getAssetPrice` (line 157) → `ChainlinkPriceOracle.getAssetPrice`.
2. `rsethAmountToMint = (amount * 0) / rsETHPrice = 0` (line 520 of `LRTDepositPool`).
3. `_beforeDeposit` checks `rsethAmountToMint < minRSETHAmountExpected` (line 667). With `minRSETHAmountExpected = 0`, `0 < 0` is false — no revert.
4. `safeTransferFrom` moves the user's LST tokens into the pool (line 114).
5. `_mintRsETH(0)` calls `RSETH.mint(user, 0)` (line 115). The `checkDailyMintLimit(0)` modifier does not revert (`0 + 0 > maxMintAmountPerDay` is false), and `_mint(user, 0)` is a no-op. The user receives nothing.

The user's LST tokens are now held by the deposit pool with no corresponding rsETH shares — the funds are frozen.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate` (lines 30–32) used for pool collateral explicitly validates:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle` is the oracle registered in `LRTOracle.assetPriceOracle` for all core LST assets and is consumed on every deposit and withdrawal.

Note: The "Critical — negative price wraps to `type(uint256).max`" sub-claim is not valid at that severity. Chainlink LST/ETH feeds enforce a `minAnswer` floor that prevents negative answers in practice, and even if it occurred, `RSETH.mint` enforces a `maxMintAmountPerDay` cap (lines 50–51 of `RSETH.sol`) that would revert before unbounded minting could drain the protocol.

## Impact Explanation

**Medium — Temporary freezing of funds:** When a Chainlink feed triggers its circuit breaker and returns `answer = 0`, any depositor who calls `depositAsset` with `minRSETHAmountExpected = 0` (a common default for automated integrators and naive callers) will have their LST tokens transferred into the pool and receive 0 rsETH. The tokens are locked in the pool with no recovery path for the user.

**Low — Contract fails to deliver promised returns:** When a feed goes stale (no update within its heartbeat window), `getAssetPrice` silently returns the last recorded price. Deposits made during this window are minted at an incorrect rate, causing share mis-accounting for all depositors relative to the true asset value.

## Likelihood Explanation

The zero-price scenario requires two concurrent conditions: (1) a Chainlink LST/ETH feed returning `answer = 0` during a circuit-breaker event — a documented and historically observed behavior — and (2) the depositor passing `minRSETHAmountExpected = 0`, which is the default for many on-chain integrators and unsophisticated users. No privileged role is required; any external caller of `depositAsset` can trigger this path. The staleness scenario requires only that a feed stops updating, which has occurred on mainnet during periods of network congestion or oracle operator issues.

## Recommendation

1. Capture `updatedAt` and `answeredInRound` from `latestRoundData` and revert if `answeredInRound < roundId` or `block.timestamp - updatedAt > heartbeat`.
2. Add `require(price > 0, "Invalid price")` before the cast.
3. Align `ChainlinkPriceOracle` with the validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.getRate`.
4. Consider adding a per-asset configurable heartbeat (e.g., 3600 s for ETH/ETH feeds, 86400 s for slower feeds) via `updatePriceFeedFor`.

## Proof of Concept

**Zero-price freeze (Medium):**

```solidity
// Fork test setup: mock the stETH/ETH Chainlink feed to return answer = 0
mockFeed.setLatestRoundData(1, 0, block.timestamp, block.timestamp, 1);

uint256 depositAmount = 1e18; // 1 stETH
stETH.approve(address(depositPool), depositAmount);

// minRSETHAmountExpected = 0 (common default)
depositPool.depositAsset(stETH, depositAmount, 0, "");

// Assert: user's stETH is gone, rsETH balance is 0
assertEq(stETH.balanceOf(user), 0);
assertEq(rsETH.balanceOf(user), 0); // funds frozen
```

**Staleness (Low):**

```solidity
// Advance time past the feed's heartbeat without a price update
vm.warp(block.timestamp + 90000); // > 86400s heartbeat

// getAssetPrice still returns the stale price — no revert
uint256 price = chainlinkOracle.getAssetPrice(stETH);
assertGt(price, 0); // stale price silently accepted
```