### Title
Unchecked Negative Chainlink Price Causes Arithmetic Overflow Revert, Freezing Withdrawal Queue and Deposits - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` casts the raw `int256` Chainlink answer directly to `uint256` without validating that the price is positive. In Solidity 0.8+, the explicit cast `uint256(negative_int256)` silently wraps to a near-`type(uint256).max` value, and the subsequent `* 1e18` multiplication triggers an arithmetic overflow panic revert. This revert propagates to every caller of `LRTOracle.getAssetPrice()`, blocking deposits, new withdrawal requests, instant withdrawals, and — most critically — the processing of the existing withdrawal unlock queue, temporarily freezing user funds.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` reads the raw `int256` answer from Chainlink and converts it without any negativity guard:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

In Solidity 0.8+, `uint256(int256(-1))` does **not** revert — it silently wraps to `2^256 - 1`. The overflow protection only applies to arithmetic operations, not explicit casts. Therefore the multiplication `(2^256 - 1) * 1e18` immediately triggers an arithmetic overflow panic revert.

This revert propagates through `LRTOracle.getAssetPrice()`: [2](#0-1) 

...into every downstream caller:

**1. `LRTDepositPool.getRsETHAmountToMint()`** — called by `depositAsset()` and `depositETH()`: [3](#0-2) 

**2. `LRTWithdrawalManager.initiateWithdrawal()`** — calls `getExpectedAssetAmount()` which calls `lrtOracle.getAssetPrice(asset)`: [4](#0-3) [5](#0-4) 

**3. `LRTWithdrawalManager.instantWithdrawal()`** — also calls `getExpectedAssetAmount()`: [6](#0-5) 

**4. `LRTWithdrawalManager.unlockQueue()`** — calls `_createUnlockParams()` which fetches `lrtOracle.getAssetPrice(asset)`: [7](#0-6) 

**5. `LRTOracle._getTotalEthInProtocol()`** — called by `updateRSETHPrice()`: [8](#0-7) 

The `ChainlinkOracleForRSETHPoolCollateral` contract (used for pool collateral) correctly guards against this with `if (ethPrice <= 0) revert InvalidPrice()`, but `ChainlinkPriceOracle` — the oracle used for L1 LST assets — has no such guard: [9](#0-8) 

---

### Impact Explanation

The most severe consequence is the freezing of the withdrawal unlock queue. When a user calls `initiateWithdrawal()`, their rsETH is transferred to `LRTWithdrawalManager` and a `WithdrawalRequest` is recorded. The operator must subsequently call `unlockQueue()` to process these requests. If `unlockQueue()` reverts due to a negative oracle price, the rsETH already held by the contract cannot be unlocked and users cannot receive their assets. `completeWithdrawal()` does not call `getAssetPrice()` and can still execute for already-unlocked requests, but no new requests can be unlocked.

Additionally, all deposits and new withdrawal initiations are blocked for the duration of the negative price.

**Impact: Medium — Temporary freezing of funds** (withdrawal queue frozen; rsETH already committed to the manager contract cannot be processed until the price recovers or the oracle is replaced).

---

### Likelihood Explanation

Negative Chainlink prices for ETH LSTs (stETH, ETHx, rETH) are extremely unlikely under normal market conditions. However, as the referenced M-5 report notes, asset prices can go negative due to carrying costs (as seen with WTI crude oil futures in April 2020). Chainlink's `int256` return type explicitly accommodates this possibility. The likelihood is low but non-zero, and the protocol has no runtime defense against it.

---

### Recommendation

Add a non-positive price check in `ChainlinkPriceOracle.getAssetPrice()` before the cast, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Add a corresponding `error InvalidPrice()` to the contract.

---

### Proof of Concept

1. Chainlink's LST/ETH price feed returns a negative `int256` answer (e.g., `-1`).
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` executes `uint256(-1) * 1e18`.
3. `uint256(-1)` = `2^256 - 1`; multiplying by `1e18` overflows → arithmetic panic revert.
4. `LRTOracle.getAssetPrice(stETH)` reverts.
5. Operator calls `unlockQueue(stETH, ...)` → `_createUnlockParams()` → `lrtOracle.getAssetPrice(stETH)` → reverts.
6. All pending stETH withdrawal requests remain locked. Users who already transferred rsETH to `LRTWithdrawalManager` via `initiateWithdrawal()` cannot receive their stETH until the oracle price recovers or the admin replaces the price oracle via `updatePriceOracleFor()`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L846-849)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```
