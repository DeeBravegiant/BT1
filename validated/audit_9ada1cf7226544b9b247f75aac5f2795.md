### Title
Stale `rsETHPrice` Enables Inflated Asset Payouts in `instantWithdrawal()` — (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal()` computes the asset payout using `lrtOracle.rsETHPrice()`, a stored value that is **not updated atomically** before the withdrawal executes. When an underlying asset's oracle price moves, the live asset price updates immediately while `rsETHPrice` remains stale. A user can exploit this discrepancy to receive more assets than their rsETH is actually worth, draining the `LRTUnstakingVault`.

---

### Finding Description

`instantWithdrawal()` calls `getExpectedAssetAmount(asset, rsETHUnstaked)`: [1](#0-0) 

which computes: [2](#0-1) 

`lrtOracle.rsETHPrice()` is a **stored, cached value** updated only when `updateRSETHPrice()` is explicitly called: [3](#0-2) [4](#0-3) 

`updateRSETHPrice()` is permissionless but is **never called atomically** before `instantWithdrawal()`. Meanwhile, `lrtOracle.getAssetPrice(asset)` fetches the **live** price from the asset's price oracle on every call.

When an underlying asset price drops (e.g., stETH/ETH rate decreases), `getAssetPrice(asset)` reflects the new lower price immediately, but `rsETHPrice` remains at the old higher value. The formula then yields:

```
underlyingToReceive = rsETHUnstaked × staleHighRsETHPrice / newLowAssetPrice
```

This is larger than the true redemption value. The user burns rsETH and receives inflated assets from `LRTUnstakingVault` with **no recalculation or cap applied** — unlike the queued withdrawal path, which applies `min(expectedAssetAmount, currentReturn)` at unlock time: [5](#0-4) [6](#0-5) 

The `instantWithdrawal()` path has no such protective cap — it redeems exactly `assetAmountUnlocked` from the vault and transfers it to the user: [7](#0-6) 

The same stale-price issue also affects `LRTDepositPool.getRsETHAmountToMint()` in the deposit direction — when an asset price rises but `rsETHPrice` is stale-low, depositors mint excess rsETH, diluting existing holders: [8](#0-7) 

---

### Impact Explanation

**High — Theft of unclaimed yield / direct asset theft from `LRTUnstakingVault`.**

The attacker burns rsETH at a stale (inflated) price and receives more underlying assets than their rsETH is worth. The vault is drained by the excess amount. No privileged access is required; the attack is executable by any unprivileged user with rsETH who calls `instantWithdrawal()` during the price-staleness window.

---

### Likelihood Explanation

**Medium.** The conditions required are:

1. A price movement in an underlying asset (routine market event — stETH, ETHx, etc. fluctuate continuously).
2. A window before `updateRSETHPrice()` is called — this window always exists since the function is not called on every block and can even revert for non-managers when the price increase exceeds `pricePercentageLimit`: [9](#0-8) 

3. `isInstantWithdrawalEnabled[asset]` being `true` and sufficient vault balance.

These conditions occur naturally during normal protocol operation. No oracle compromise or governance capture is required.

---

### Recommendation

Call `_updateRsETHPrice()` (or an equivalent internal update) atomically at the start of `instantWithdrawal()` before computing `assetAmountUnlocked`. Alternatively, compute the payout directly from live TVL rather than the cached `rsETHPrice`, or apply the same `min(expectedAmount, currentReturn)` cap used in the queued withdrawal path.

---

### Proof of Concept

1. **T0**: stETH oracle price = 1.05 ETH, `rsETHPrice` = 1.05 ETH (recently updated; assume 1:1 rsETH:stETH for simplicity).
2. **T1**: stETH oracle price drops to 1.00 ETH (live oracle updates immediately). `rsETHPrice` remains 1.05 ETH (stale).
3. Attacker calls `instantWithdrawal(stETH, 100e18)` before `updateRSETHPrice()` is called.
4. `assetAmountUnlocked = 100e18 × 1.05e18 / 1.00e18 = 105e18 stETH`
5. True redemption value: `100e18 × 1.00e18 / 1.00e18 = 100e18 stETH`
6. Attacker receives **5 stETH more than deserved**, draining the vault by that excess.
7. Attacker can repeat across multiple assets and multiple calls until the vault's instant-withdrawal liquidity is exhausted.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L229-251)
```text
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L824-834)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
