Audit Report

## Title
Stale `rsETHPrice` Enables Over-Minting of rsETH When Asset Oracle Prices Increase — (File: contracts/LRTOracle.sol, contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` divides a live asset oracle price by the stored `rsETHPrice` state variable. When `pricePercentageLimit` is non-zero and an LST asset price rises beyond that threshold, `updateRSETHPrice()` reverts for non-managers, leaving `rsETHPrice` stale at the old lower value. Any depositor can then call `depositAsset()` during this window and receive more rsETH than the fair value of their deposit, diluting existing rsETH holders' accrued yield.

## Finding Description
`getRsETHAmountToMint()` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.getAssetPrice(asset)` is a live read from an external price oracle: [2](#0-1) 

`rsETHPrice` is a stored state variable updated only when `_updateRsETHPrice()` is called: [3](#0-2) 

`_updateRsETHPrice()` contains a guard: when the newly computed price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, non-manager callers revert with `PriceAboveDailyThreshold`: [4](#0-3) 

This creates a persistent staleness window:
1. An LST asset oracle price increases by more than `pricePercentageLimit` (e.g., >1%).
2. Any call to `updateRSETHPrice()` by a non-manager reverts.
3. `rsETHPrice` remains at the old, lower stored value.
4. `getAssetPrice(asset)` returns the new, higher live value.
5. `getRsETHAmountToMint()` returns `amount * newHigherPrice / oldLowerRsETHPrice`, which exceeds the fair mint amount.
6. The depositor receives excess rsETH, diluting existing holders.

`_beforeDeposit()` performs no staleness check on `rsETHPrice` and does not refresh it before computing the mint amount: [5](#0-4) 

The window persists until a manager calls `updateRSETHPriceAsManager()`: [6](#0-5) 

## Impact Explanation
Every rsETH minted above fair value dilutes the share of the protocol's TVL held by existing rsETH holders. The excess rsETH represents a claim on ETH that was not contributed — extracted from the yield accrued by existing holders since the last `rsETHPrice` update. This matches **High — Theft of unclaimed yield**: the yield accrued by existing holders (the LST price appreciation) is partially transferred to the depositor via the inflated mint ratio.

## Likelihood Explanation
- `pricePercentageLimit` is a configurable admin parameter. When set to any non-zero value, a natural LST price appreciation event of that magnitude triggers the blocking condition.
- LST/ETH rates (stETH, cbETH, rETH) experience step-changes of 0.5–1%+ around reward distribution events.
- `updateRSETHPrice()` is public, so the staleness window is normally short; however, the `pricePercentageLimit` guard structurally prevents non-managers from closing it during exactly the events where the mispricing is largest.
- No privileged access, front-running, or oracle manipulation is required. Any depositor can exploit the window by calling `depositAsset()` while `rsETHPrice` is stale.

## Recommendation
Before computing `rsethAmountToMint`, atomically attempt to refresh `rsETHPrice` within `_beforeDeposit()` (catching and ignoring `PriceAboveDailyThreshold` only if the caller is not a manager, or alternatively always allowing the manager path). Alternatively, derive the mint amount directly from the live TVL and rsETH supply without relying on the stored `rsETHPrice` state variable. A simpler mitigation is to enforce that `rsETHPrice` was updated within the current block (or within a short staleness bound) before any deposit is accepted.

## Proof of Concept
**Setup:**
- `pricePercentageLimit` = `1e16` (1%)
- `rsETHPrice` = `1.04e18` (last stored value), `highestRsethPrice` = `1.04e18`
- Protocol holds 100 stETH; rsETH supply = 100
- stETH/ETH oracle price increases to `1.055e18` (~1.4% increase, above the limit)

**Step 1:** Anyone calls `updateRSETHPrice()`.
- `newRsETHPrice` ≈ `1.055e18` (computed from live TVL: 100 × 1.055 / 100)
- `priceDifference = 1.055e18 - 1.04e18 = 0.015e18`
- `pricePercentageLimit.mulWad(highestRsethPrice) = 0.01 × 1.04e18 = 0.0104e18`
- `0.015e18 > 0.0104e18` → `isPriceIncreaseOffLimit = true`
- Caller is not a manager → **reverts with `PriceAboveDailyThreshold`**
- `rsETHPrice` remains `1.04e18`

**Step 2:** Attacker calls `depositAsset(stETH, 100e18, 0, "")`.
- `getRsETHAmountToMint(stETH, 100e18)` = `100e18 × 1.055e18 / 1.04e18` ≈ **`101.44e18` rsETH**
- Fair mint amount = `100e18 × 1.055e18 / 1.055e18` = `100e18` rsETH
- **Excess rsETH minted: ~`1.44e18`**

**Step 3:** Manager calls `updateRSETHPriceAsManager()`. New rsETHPrice is computed from updated TVL (200 stETH × 1.055 / 201.44 supply ≈ `1.04748e18`). The attacker's `101.44` rsETH is worth `101.44 × 1.04748 ≈ 106.25 ETH`, while they deposited 100 stETH worth `105.5 ETH`. **Net gain: ~0.75 ETH extracted from existing rsETH holders per 100 stETH deposited.** The gain scales linearly with deposit size and the magnitude of the staleness gap.

### Citations

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```
