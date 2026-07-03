### Title
Unvalidated Chainlink `int256` Price Cast to `uint256` Enables Temporary Fund Freeze - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` retrieves `int256 price` from Chainlink's `latestRoundData()` and casts it directly to `uint256` without verifying `price > 0`. This is the direct Solidity analog of the reported bug: a value not guaranteed to be valid is used without a prior existence/validity check. A zero or negative price propagates into the deposit, withdrawal, and oracle-update paths, causing temporary fund freezes.

### Finding Description
In `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no guard of the form `require(price > 0, ...)` before the cast. Chainlink's `latestRoundData()` returns `int256` and can legitimately return `0` (deprecated aggregator, circuit-breaker round) or a negative value. The code silently accepts both.

**Case 1 — `price == 0`:** `getAssetPrice` returns `0`.
- `LRTDepositPool.getRsETHAmountToMint()` computes `(amount * 0) / rsETHPrice = 0`; a depositor who passes `minRSETHAmountExpected = 0` deposits their LST and receives zero rsETH (funds stuck in the pool).
- `LRTWithdrawalManager.getExpectedAssetAmount()` computes `amount * rsETHPrice / 0`, which reverts with a division-by-zero, freezing all withdrawal initiations for that asset.
- `LRTOracle._getTotalEthInProtocol()` omits the affected asset's TVL contribution; the resulting `newRsETHPrice` drop can exceed `pricePercentageLimit`, triggering the automatic protocol-wide pause (`lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`).

**Case 2 — `price < 0`:** `uint256(price)` wraps to a value near `type(uint256).max`. Any subsequent multiplication (e.g., `amount * uint256(price)`) overflows and reverts under Solidity 0.8.x checked arithmetic, freezing every code path that calls `getAssetPrice`.

### Impact Explanation
**Medium — Temporary freezing of funds.**

A zero price from any supported asset's Chainlink feed causes:
1. Withdrawal initiation to revert (division by zero in `getExpectedAssetAmount`).
2. Deposits to silently mint 0 rsETH if the caller omits a slippage guard.
3. `updateRSETHPrice()` (publicly callable) to compute an artificially low rsETH price, potentially triggering the automatic protocol pause that locks both deposits and withdrawals until an admin manually unpauses.

A negative price causes overflow reverts across all oracle-dependent paths, achieving the same freeze.

### Likelihood Explanation
**Low-to-Medium.** Chainlink price feeds for major LSTs (stETH/ETH, ETHx/ETH) rarely return zero or negative values on mainnet. However, the condition is reachable in documented edge cases: a deprecated aggregator round, a Chainlink circuit-breaker activation, or a misconfigured feed set via `updatePriceOracleFor`. No attacker action is required beyond calling the already-public `updateRSETHPrice()` after the feed returns an invalid value.

### Recommendation
Add a positivity check in `ChainlinkPriceOracle.getAssetPrice()` before casting:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
require(price > 0, "ChainlinkPriceOracle: invalid price");
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

This mirrors the `hasProperty` / existence-check recommendation in the original report: validate the value is defined and valid before consuming it.

### Proof of Concept

1. Chainlink's `latestRoundData()` for a supported LST (e.g., stETH/ETH) returns `price = 0` (circuit-breaker or deprecated round).
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0` — no revert, no check.
3. Any user calls `LRTWithdrawalManager.initiateWithdrawal(stETH, amount, ...)`:
   - `getExpectedAssetAmount(stETH, amount)` → `amount * rsETHPrice / 0` → **revert** (division by zero). All stETH withdrawal initiations are frozen.
4. Any user calls `LRTOracle.updateRSETHPrice()`:
   - `_getTotalEthInProtocol()` returns TVL with stETH contribution = 0.
   - `newRsETHPrice` drops sharply; if `pricePercentageLimit` is set, `lrtDepositPool.pause()` and `withdrawalManager.pause()` are called automatically — **entire protocol is temporarily frozen**. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
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
            }
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```
