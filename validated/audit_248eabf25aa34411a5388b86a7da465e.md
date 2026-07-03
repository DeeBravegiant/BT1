### Title
Unchecked Zero Return from `getAssetPrice()` Causes Silent Zero-Mint on Deposit — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` and casts the returned `int256 price` directly to `uint256` without validating that `price > 0`. If Chainlink returns `0` (e.g., a deprecated feed, circuit-breaker event, or feed failure), the function silently returns `0`. Every caller that multiplies by this return value — most critically `LRTDepositPool.getRsETHAmountToMint()` — then silently computes `rsethAmountToMint = 0`. A depositor who passes `minRSETHAmountExpected = 0` will have their assets transferred to the protocol while receiving zero rsETH, permanently locking their funds with no redemption path.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` performs no validation on the price returned by Chainlink:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-L55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

There is no check that `price > 0`. If `price == 0`, the function returns `0` silently. [1](#0-0) 

This zero propagates into `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol L519-L521
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

When `getAssetPrice(asset)` returns `0`, `rsethAmountToMint` evaluates to `0`. [2](#0-1) 

The slippage guard in `_beforeDeposit` only reverts if `rsethAmountToMint < minRSETHAmountExpected`:

```solidity
// contracts/LRTDepositPool.sol L665-L669
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

When `minRSETHAmountExpected = 0` (a common default), `0 < 0` is false — no revert occurs. [3](#0-2) 

Execution then continues in `depositAsset()`:

```solidity
// contracts/LRTDepositPool.sol L113-L116
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint); // mints 0
```

The user's assets are transferred to the protocol, and `mint(user, 0)` is called — the user receives no rsETH and has no on-chain claim to recover their deposit. [4](#0-3) 

The same zero-propagation affects `LRTOracle._getTotalEthInProtocol()`, where a zero asset price causes that asset's entire TVL contribution to be silently dropped from the rsETH price calculation:

```solidity
// contracts/LRTOracle.sol L339-L343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER); // adds 0
```

This causes `rsETHPrice` to be computed lower than reality, potentially triggering the downside-protection pause. [5](#0-4) 

Note: the sanity check in `updatePriceOracleForValidated()` only validates the price at oracle registration time, not at query time — the price can move to 0 after registration. [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

A depositor who calls `depositAsset(asset, amount, 0, referralId)` while the Chainlink feed returns `0` will have `amount` of their LST transferred to `LRTDepositPool` and receive `0` rsETH in return. Because the withdrawal system (`LRTWithdrawalManager.initiateWithdrawal`) requires the user to hold rsETH to initiate a withdrawal, the depositor has no protocol-sanctioned path to recover their assets. The funds are permanently locked in the deposit pool from the user's perspective.

---

### Likelihood Explanation

**Low-Medium.** Two conditions must coincide:

1. A supported Chainlink price feed returns `0` — possible during feed deprecation, a circuit-breaker event, or a feed migration window. Chainlink feeds have historically returned `0` or stale data during such transitions.
2. The depositor passes `minRSETHAmountExpected = 0` — a realistic default for integrators, bots, or users who do not understand the slippage parameter.

Neither condition requires privileged access or attacker-controlled state. An unprivileged depositor is the victim; the trigger is an external feed anomaly combined with a missing on-chain guard.

---

### Recommendation

Add a `price > 0` guard in `ChainlinkPriceOracle.getAssetPrice()` and revert on invalid data, mirroring the fix applied in the referenced starknet-snap report (raise an error instead of returning an invalid value):

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, add a staleness check (`updatedAt`) and a round-completeness check (`answeredInRound >= roundId`). Consider also adding a non-zero guard in `getRsETHAmountToMint()` as a defense-in-depth measure.

---

### Proof of Concept

1. Chainlink feed for asset `X` returns `price = 0` (feed deprecated or circuit-breaker active).
2. Attacker (or innocent user) calls:
   ```solidity
   LRTDepositPool.depositAsset(X, 1e18, 0, "");
   ```
3. `_beforeDeposit` calls `getRsETHAmountToMint(X, 1e18)`.
4. `lrtOracle.getAssetPrice(X)` → `ChainlinkPriceOracle.getAssetPrice(X)` → returns `0`.
5. `rsethAmountToMint = (1e18 * 0) / rsETHPrice = 0`.
6. `0 < 0` is false → no revert.
7. `IERC20(X).safeTransferFrom(user, depositPool, 1e18)` — user's 1 LST transferred.
8. `IRSETH.mint(user, 0)` — user receives 0 rsETH.
9. User holds 0 rsETH; `LRTWithdrawalManager.initiateWithdrawal` requires rsETH balance → user cannot withdraw. Funds permanently locked.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L113-116)
```text
        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L101-108)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
