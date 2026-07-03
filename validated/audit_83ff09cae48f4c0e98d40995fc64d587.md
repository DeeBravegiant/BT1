### Title
Stale Cached `rsETHPrice` Used in Deposit Minting Without Prior Update - (`contracts/LRTOracle.sol` / `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTOracle.rsETHPrice` is a cached storage variable that is only updated when `updateRSETHPrice()` is explicitly called. The deposit flow in `LRTDepositPool` reads this stale cached value directly — without first triggering an update — to determine how many rsETH tokens to mint. This is the direct analog of the Blueberry `exchangeRateStored()` bug: a stored rate is consumed in a critical financial calculation without first refreshing it to reflect accrued value.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate in a plain state variable: [1](#0-0) 

This variable is only updated when `updateRSETHPrice()` (or `updateRSETHPriceAsManager()`) is explicitly called: [2](#0-1) 

The deposit path in `LRTDepositPool` calls `getRsETHAmountToMint()`, which reads `lrtOracle.rsETHPrice()` directly — the cached storage value — without first calling `updateRSETHPrice()`: [3](#0-2) 

`_beforeDeposit()` calls `getRsETHAmountToMint()` and this is the sole price source for both `depositETH` and `depositAsset`: [4](#0-3) 

Between oracle update calls, the underlying LST assets (stETH, ETHx, rETH, etc.) continuously accrue staking rewards, increasing the true value of rsETH. However, `rsETHPrice` remains frozen at the last-updated value. The minting formula `rsethAmountToMint = (amount * assetPrice) / rsETHPrice` uses this stale denominator.

The same stale price is consumed in `_createUnlockParams()` for the withdrawal unlock path: [5](#0-4) 

Additionally, `_updateRsETHPrice()` contains a `pricePercentageLimit` guard that **reverts** for non-manager callers if the price increase exceeds the configured threshold: [6](#0-5) 

This means that after a period of significant reward accrual, regular users cannot call `updateRSETHPrice()` to refresh the price — the price stays stale-low for longer, widening the exploitable window.

---

### Impact Explanation

When `rsETHPrice` is stale-low (actual value has grown due to accrued staking rewards but the stored price has not been updated):

- The minting formula `(amount * assetPrice) / rsETHPrice` produces a **larger** rsETH amount than deserved.
- A depositor receives more rsETH than their proportional share of the protocol's TVL warrants.
- This dilutes the value held by all existing rsETH holders — effectively transferring accrued yield from existing holders to the new depositor.

This constitutes **theft of unclaimed yield** from existing rsETH holders.

**Impact: Medium** — Theft of unclaimed yield.

---

### Likelihood Explanation

- The price staleness window exists between every oracle update cycle. The protocol relies on off-chain keepers to call `updateRSETHPrice()`.
- Any depositor can observe on-chain when the last update occurred and the current LST prices, compute the true rsETH value, and deposit during the stale window.
- The `pricePercentageLimit` guard can extend the stale window by blocking non-manager updates when rewards have accumulated beyond the threshold.
- No special privileges are required; any unprivileged depositor can exploit this.

**Likelihood: Medium** — Requires timing a deposit to the staleness window, which is predictable and observable on-chain.

---

### Recommendation

Before computing the rsETH amount to mint in `getRsETHAmountToMint()`, the protocol should call `updateRSETHPrice()` to refresh the stored price. Since `updateRSETHPrice()` is a state-mutating function, `getRsETHAmountToMint()` must be converted from a `view` function to a regular function, and the deposit entry points (`depositETH`, `depositAsset`) must call it before minting. Alternatively, compute the rsETH price on-the-fly (without caching) inside the deposit flow using `_getTotalEthInProtocol()` directly, analogous to how `exchangeRateCurrent()` accrues interest before returning the rate.

---

### Proof of Concept

1. At time T, `updateRSETHPrice()` is called. `rsETHPrice = 1.05e18` (rsETH is worth 1.05 ETH).
2. Over the next several hours, stETH and ETHx accrue staking rewards. The true rsETH value rises to `1.06e18`, but `rsETHPrice` remains `1.05e18`.
3. The `pricePercentageLimit` guard prevents a non-manager from calling `updateRSETHPrice()` (if the increase exceeds the limit), keeping the price stale.
4. Attacker calls `depositETH(0, "")` with 100 ETH.
5. `getRsETHAmountToMint` computes: `(100e18 * 1e18) / 1.05e18 ≈ 95.24 rsETH` instead of the correct `(100e18 * 1e18) / 1.06e18 ≈ 94.34 rsETH`.
6. Attacker receives ~0.9 extra rsETH, representing accrued yield stolen from existing holders.
7. After `updateRSETHPrice()` is eventually called, the attacker's rsETH is worth the correct higher price, having captured yield that belonged to prior depositors. [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

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

**File:** contracts/LRTOracle.sol (L252-265)
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

**File:** contracts/LRTDepositPool.sol (L648-665)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L846-851)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```
