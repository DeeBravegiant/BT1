Audit Report

## Title
Missing Staleness Check in `ChainlinkPriceOracle.getAssetPrice()` Causes Users to Receive Fewer Assets Than Fair Value in `instantWithdrawal` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness indicators, accepting any price regardless of age. When `LRTWithdrawalManager.instantWithdrawal()` consumes this stale price, users burn rsETH irreversibly and receive fewer underlying assets than the fair value of their rsETH, with no `minAmountOut` parameter to protect them. The protocol's own `ChainlinkOracleForRSETHPoolCollateral` already implements the missing staleness checks, confirming this is a known pattern that was not applied consistently.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` silently discards all staleness indicators returned by `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` timestamp and `answeredInRound` fields are never validated. By contrast, the protocol's own `ChainlinkOracleForRSETHPoolCollateral` performs both checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`LRTWithdrawalManager.instantWithdrawal()` calls `getExpectedAssetAmount()`, which divides by the stale asset price:

```solidity
// contracts/LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

The rsETH burn occurs immediately after the amount is computed, before any slippage check:

```solidity
// contracts/LRTWithdrawalManager.sol L228-229
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

`instantWithdrawal()` accepts only `asset`, `rsETHUnstaked`, and `referralId` — there is no `minAssetAmountOut` parameter. This contrasts with `LRTDepositPool.depositETH()`, which accepts `minRSETHAmountExpected` for slippage protection:

```solidity
// contracts/LRTDepositPool.sol L76-77
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
```

The `unlockQueue()` operator path does apply price-range guards via `_validatePrices()`, but `instantWithdrawal()` does not call `_validatePrices()` and has no equivalent protection.

**Exploit flow:**
1. A Chainlink LST/ETH feed (e.g., stETH/ETH, 24-hour heartbeat) becomes stale during network congestion or a brief oracle outage.
2. The last reported price is inflated relative to the current market (e.e., 1.05e18 vs. real 1.00e18 after a slashing event).
3. Any rsETH holder calls `instantWithdrawal(stETH, rsETHAmount, "")`.
4. `getExpectedAssetAmount` computes `rsETHAmount * rsETHPrice / 1.05e18`, yielding ~4.76% fewer stETH than fair value.
5. rsETH is burned at L229; the user cannot recover the shortfall.
6. No revert occurs because `ChainlinkPriceOracle` never checks `updatedAt` and `instantWithdrawal` has no `minAmountOut` guard.

## Impact Explanation

A user calling `instantWithdrawal` during a period of Chainlink feed staleness burns rsETH irreversibly but receives fewer underlying assets than the fair value of that rsETH. The shortfall accrues to the unstaking vault, benefiting remaining participants at the expense of the instant withdrawer. The burn is permanent and unrecoverable. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation

Chainlink heartbeats for LST/ETH feeds are typically 24 hours. During periods of network congestion, rapid LST price movement, or a temporary oracle outage, the on-chain price can lag the real price by a meaningful margin. The function is publicly callable by any rsETH holder whenever instant withdrawal is enabled for an asset. No attacker capability is required — any user transacting during a stale-price window is silently penalized. **Likelihood: Medium.**

## Recommendation

1. Add heartbeat and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:
   ```solidity
   (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
       priceFeed.latestRoundData();
   if (answeredInRound < roundId) revert StalePrice();
   if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();
   if (price <= 0) revert InvalidPrice();
   ```
2. Add a `minAssetAmountOut` parameter to `instantWithdrawal()` so users can specify the minimum acceptable asset amount and revert if the oracle-derived amount falls below it, analogous to `minRSETHAmountExpected` in `LRTDepositPool.depositETH()`.

## Proof of Concept

**Foundry fork test outline:**

```solidity
function test_instantWithdrawal_stalePrice() public {
    // 1. Fork mainnet; obtain rsETH holder
    // 2. Mock the stETH/ETH Chainlink feed to return a price last updated 25 hours ago
    //    (beyond the 24-hour heartbeat), with price = 1.05e18
    // 3. Set real market price to 1.00e18 (simulate 5% drop)
    // 4. Record user's stETH balance before call
    // 5. Call instantWithdrawal(stETH, 1e18, "")
    // 6. Assert: rsETH burned == 1e18 (irreversible)
    // 7. Assert: stETH received ≈ rsETHPrice/1.05e18 (< rsETHPrice/1.00e18)
    // 8. Assert: no revert occurred despite stale price
    // 9. Compute shortfall = (rsETHPrice/1.00e18) - (rsETHPrice/1.05e18) > 0
}
```

**Minimal call sequence (no fork):**
1. Deploy `ChainlinkPriceOracle` with a mock feed returning `updatedAt = block.timestamp - 25 hours`, `price = 1.05e18`.
2. Call `getAssetPrice(stETH)` — returns `1.05e18` with no revert.
3. Call `instantWithdrawal(stETH, 1e18, "")` — rsETH burned, user receives `rsETHPrice / 1.05e18` stETH instead of `rsETHPrice / 1.00e18`.
4. Confirm shortfall is non-zero and rsETH burn is irreversible.