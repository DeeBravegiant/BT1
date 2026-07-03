### Title
Unchecked Negative Chainlink `int256` Price Cast to `uint256` Freezes All Deposits and Withdrawals - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` casts the `int256 answer` returned by Chainlink's `latestRoundData()` directly to `uint256` without any positivity check. If Chainlink returns a negative price, the explicit cast silently produces a near-`2^256` garbage value, and the subsequent `* 1e18` multiplication overflows and reverts in Solidity 0.8+. This DoS propagates to every protocol function that depends on asset pricing: deposits, withdrawals, and instant withdrawals.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` is:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Chainlink's `latestRoundData()` is declared to return `int256 answer`. The Chainlink specification does not guarantee the value is positive; it can be zero or negative in degraded/misconfigured feed states. In Solidity 0.8+, an explicit `uint256(negativeInt256)` cast does **not** revert — it silently wraps to a value near `2^256`. The immediately following arithmetic `uint256(price) * 1e18` then triggers a checked-arithmetic overflow revert.

This revert propagates through every caller of `getAssetPrice()`:

- `LRTDepositPool.getRsETHAmountToMint()` → `depositAsset()` / `depositETH()` revert [2](#0-1) 

- `LRTWithdrawalManager.getExpectedAssetAmount()` → `initiateWithdrawal()` / `instantWithdrawal()` revert [3](#0-2) 

- `LRTWithdrawalManager._calculatePayoutAmount()` → `unlockQueue()` reverts [4](#0-3) 

The root cause is structurally identical to M-47: an `int256` value that can be negative is explicitly cast to `uint256` without a prior positivity check, corrupting or blocking downstream accounting.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** While the negative price persists, no user can deposit any supported LST or ETH, initiate a withdrawal, complete an instant withdrawal, or have their queued withdrawal unlocked. Funds already in the protocol are not directly stolen, but they are inaccessible until the oracle feed recovers or is replaced by governance action. The freeze affects all supported collateral assets simultaneously because `getAssetPrice()` is the shared pricing primitive.

---

### Likelihood Explanation

Chainlink price feeds for major LSTs (stETH/ETH, ETHx/ETH, rETH/ETH, sfrxETH/ETH) do not routinely return negative values, but the Chainlink interface explicitly types the answer as `int256`, leaving the door open. Feed misconfiguration, a newly added feed with incorrect decimals, or an extreme market dislocation could produce a zero or negative answer. No attacker control is required — the condition can arise from oracle infrastructure alone, making it a realistic operational risk rather than a purely theoretical one.

---

### Recommendation

Add an explicit positivity check before casting:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
require(price > 0, "ChainlinkPriceOracle: invalid price");
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

This mirrors the fix recommended in M-47: validate the signed value before any conversion to an unsigned type.

---

### Proof of Concept

1. A Chainlink price feed for a supported LST (e.g., stETH) returns `price = -1` due to feed misconfiguration or an extreme market event.
2. Any user calls `LRTDepositPool.depositAsset(stETH, amount, minRSETH, "")`.
3. Internally, `getRsETHAmountToMint(stETH, amount)` calls `lrtOracle.getAssetPrice(stETH)`.
4. `ChainlinkPriceOracle.getAssetPrice(stETH)` executes:
   - `uint256(-1)` = `115792089237316195423570985008687907853269984665640564039457584007913129639935`
   - `115792...935 * 1e18` → checked-arithmetic overflow → **revert**
5. The revert bubbles up: `depositAsset` reverts. The same happens for `depositETH`, `initiateWithdrawal`, `instantWithdrawal`, and `unlockQueue`.
6. All user-facing fund operations are frozen until the oracle feed is corrected or replaced via governance. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
