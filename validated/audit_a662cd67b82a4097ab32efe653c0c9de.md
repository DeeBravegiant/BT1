Audit Report

## Title
Chainlink Price Not Validated for Zero/Negative Value in `getAssetPrice` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` fetches `int256 price` from Chainlink's `latestRoundData()` and casts it directly to `uint256` without any non-positive guard. A zero price propagates into deposit minting (user receives 0 rsETH for deposited assets) and withdrawal payout (division by zero reverts all pending withdrawals for that asset). The same repository already applies `if (ethPrice <= 0) revert InvalidPrice()` in `ChainlinkOracleForRSETHPoolCollateral`, confirming protocol awareness of the pattern.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at L49–55:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check on `price <= 0` exists. Two failure modes:

- **`price == 0`**: `uint256(0)` is returned and propagates through `LRTOracle.getAssetPrice()` (L156–158), which is a pure delegation with no additional guard.
- **`price < 0`**: Solidity's bitwise reinterpretation of a negative `int256` as `uint256` produces `type(uint256).max`, massively inflating the reported price.

Downstream consumers:

1. **`LRTDepositPool.getRsETHAmountToMint()`** (L520): `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. With `getAssetPrice == 0`, the user's deposited assets are transferred in but `0` rsETH is minted — the user has no claim on their funds.

2. **`LRTWithdrawalManager._calculatePayoutAmount()`** (L833): `(request.rsETHUnstaked * rsETHPrice) / assetPrice`. With `assetPrice == 0`, this is a division by zero, reverting every withdrawal for that asset.

3. **`LRTOracle._getTotalEthInProtocol()`** (L339–343): `totalETHInProtocol += totalAssetAmt.mulWad(assetER)`. Zero price silently removes that asset's entire ETH contribution from the TVL calculation, causing `newRsETHPrice` to be computed too low.

The `updatePriceOracleForValidated` sanity check (L103–106) only validates the price at oracle registration time; it provides no protection against the feed returning 0 during a subsequent circuit-breaker event.

## Impact Explanation
**Critical — Direct loss of user funds.** When a Chainlink feed returns `price = 0`, a depositor calling `depositAsset` transfers their LST into the protocol and receives `0` rsETH. With no rsETH minted, the depositor has no mechanism to reclaim their deposited assets. Concurrently, all pending withdrawals for that asset revert (temporary freeze). The negative-price path inflates `totalETHInProtocol`, causing rsETH price to spike and either triggering `PriceAboveDailyThreshold` for non-manager callers or minting excessive fee rsETH to treasury.

## Likelihood Explanation
Chainlink circuit breakers are a documented, real-world event (e.g., LUNA crash, stETH depeg). No attacker action is required — any depositor or withdrawer interacting with the protocol during such an event triggers the impact. The protocol's own `ChainlinkOracleForRSETHPoolCollateral` (L32) already applies `if (ethPrice <= 0) revert InvalidPrice()`, confirming the team is aware of this risk class. The omission in `ChainlinkPriceOracle` is an inconsistency that can be triggered by any Chainlink feed anomaly.

## Recommendation
Add a non-positive price guard immediately after fetching the price:

```diff
 (, int256 price,,,) = priceFeed.latestRoundData();
+if (price <= 0) revert InvalidPrice();
 return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Mirror the staleness checks (`answeredInRound < roundID`, `timestamp == 0`) already present in `ChainlinkOracleForRSETHPoolCollateral` for full consistency.

## Proof of Concept
1. Chainlink's `latestRoundData()` for a supported LST (e.g., stETH/ETH) returns `price = 0` during a circuit-breaker event.
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `uint256(0) * 1e18 / decimals = 0`.
3. `LRTOracle.getAssetPrice(stETH)` returns `0` (no additional guard).
4. User calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, referral)`:
   - `getRsETHAmountToMint(stETH, 1e18)` → `(1e18 * 0) / rsETHPrice = 0`
   - `1e18` stETH is transferred from user to protocol; `0` rsETH is minted.
   - User's funds are permanently inaccessible.
5. Alternatively, user calls `LRTWithdrawalManager.unlockQueue(stETH, ...)`:
   - `_createUnlockParams` fetches `assetPrice = 0`.
   - `_calculatePayoutAmount` executes `(rsETHUnstaked * rsETHPrice) / 0` → division-by-zero revert.
   - All pending stETH withdrawals are frozen until the oracle recovers.

**Foundry fork test outline:**
```solidity
// Fork mainnet, mock stETH/ETH Chainlink feed to return answer = 0
vm.mockCall(stEthFeed, abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
    abi.encode(1, int256(0), block.timestamp, block.timestamp, 1));
// Deposit 1e18 stETH, assert rsETH minted == 0 and stETH balance of pool increased
uint256 minted = depositPool.depositAsset(stETH, 1e18, 0, address(0));
assertEq(minted, 0);
```