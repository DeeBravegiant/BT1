Audit Report

## Title
Missing Chainlink Price Feed Validation Allows Stale or Zero Price to Corrupt rsETH Minting and Withdrawal Calculations - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation fields and does not check whether the returned price is zero or negative. A zero price propagates directly into rsETH minting calculations (yielding 0 rsETH for real deposited assets when `minRSETHAmountExpected` is 0) and causes division-by-zero in withdrawal payout calculations, temporarily freezing withdrawals for the affected asset. The sibling contract `ChainlinkOracleForRSETHPoolCollateral` already implements all three required checks, confirming the protocol is aware of the requirement.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the asset/ETH exchange rate without any validation:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

All five return values `(roundId, answer, startedAt, updatedAt, answeredInRound)` are available but only `price` is used. No checks are performed for `price <= 0`, `answeredInRound < roundId`, or `updatedAt == 0`.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` performs all three critical checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`LRTOracle.getAssetPrice()` delegates directly to `ChainlinkPriceOracle` with no additional validation:

```solidity
// contracts/LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
```

This unvalidated price is consumed in two critical paths:

**Deposit path** (`contracts/LRTDepositPool.sol` L520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
If `getAssetPrice` returns 0, `rsethAmountToMint = 0`. The slippage guard `if (rsethAmountToMint < minRSETHAmountExpected)` only reverts if the caller passes a non-zero minimum. A caller passing `minRSETHAmountExpected = 0` (common in direct contract calls and many front-ends) will have their assets transferred in while receiving 0 rsETH.

**Withdrawal path** (`contracts/LRTWithdrawalManager.sol` L593):
```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
If `getAssetPrice` returns 0, this is an unconditional division-by-zero revert. Every withdrawal unlock attempt for the affected asset reverts, freezing those funds until the feed recovers.

**TVL path** (`contracts/LRTOracle.sol` L339-343): A zero price causes the affected asset's ETH value to be counted as 0, corrupting `rsETHPrice` downward and potentially triggering the downside-protection pause.

## Impact Explanation
- **Temporary freezing of funds (Medium)**: When a Chainlink feed returns `price = 0`, all calls to `LRTWithdrawalManager.getUnderlyingAmount()` for that asset revert with division-by-zero. Users with pending withdrawal requests for that asset cannot unlock their funds until the feed recovers. This is a concrete, unconditional freeze requiring no victim action beyond having a pending withdrawal.
- **Direct loss of deposited funds (Critical)**: A depositor calling `depositAsset` with `minRSETHAmountExpected = 0` while the feed returns 0 will transfer real LST assets into the protocol and receive 0 rsETH. The protocol does not enforce a non-zero minimum, and the slippage parameter is caller-controlled. The asset transfer is permanent.

## Likelihood Explanation
Low. Requires a Chainlink price feed to return `price = 0` or a stale answer. This can occur during feed initialization, aggregator migration, or a prolonged network outage where `answeredInRound < roundId`. Chainlink documentation explicitly warns that `latestRoundData` can return stale data and recommends staleness checks. No privileged access or active attacker is required — the condition arises from external feed behavior. The withdrawal freeze path requires only that a user has a pending withdrawal when the feed degrades; the deposit loss path additionally requires the caller to pass `minRSETHAmountExpected = 0`.

## Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Optionally add a heartbeat staleness check: `if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();`

## Proof of Concept

**Withdrawal freeze (Medium — no victim mistake required):**
1. Deploy a mock Chainlink aggregator for ETHx/ETH that returns `price = 0`.
2. Register it via `ChainlinkPriceOracle.updatePriceFeedFor(ETHx, mockFeed)`.
3. A user with a pending withdrawal request calls `LRTWithdrawalManager.unlockQueue(ETHx, n)`.
4. Internally calls `getUnderlyingAmount(rsETHAmount, ETHx)` → `lrtOracle.getAssetPrice(ETHx)` → `ChainlinkPriceOracle.getAssetPrice(ETHx)` → returns `0`.
5. `underlyingToReceive = amount * rsETHPrice / 0` → division-by-zero revert.
6. All withdrawal unlocks for ETHx are frozen until the feed recovers.

**Deposit loss (Critical — requires `minRSETHAmountExpected = 0`):**
1. Same mock feed returning `price = 0`.
2. User calls `LRTDepositPool.depositAsset(ETHx, 10 ether, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(ETHx, 10e18)` → `(10e18 * 0) / rsETHPrice = 0`.
4. Slippage check: `0 < 0` is false → passes.
5. `IERC20(ETHx).safeTransferFrom(user, depositPool, 10e18)` executes.
6. `_mintRsETH(0)` mints 0 rsETH to user.
7. User's 10 ETHx (~10 ETH value) is permanently locked in the protocol with no rsETH receipt.

**Foundry fork test plan**: Fork mainnet, mock `latestRoundData` on the ETHx/ETH Chainlink feed to return `(1, 0, block.timestamp, block.timestamp, 1)`, then call `depositAsset` with `minRSETHAmountExpected = 0` and assert the user receives 0 rsETH while their balance decreases by the deposit amount.