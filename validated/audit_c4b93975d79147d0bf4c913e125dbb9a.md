### Title
Stale `rsETHPrice` in `LRTOracle` Allows Depositors to Mint Excess rsETH at the Expense of Existing Holders — (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint()` divides a live asset price by a **stored, potentially stale** `rsETHPrice`. Because `rsETHPrice` is only updated when `updateRSETHPrice()` is explicitly called, a window always exists between when underlying assets appreciate and when the stored price catches up. Any depositor who acts during this window receives more rsETH than they are entitled to, diluting existing holders and extracting their accrued yield.

---

### Finding Description

`LRTDepositPool.getRsETHAmountToMint()` computes:

```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()
``` [1](#0-0) 

`lrtOracle.getAssetPrice(asset)` reads **live** from an external price fetcher, while `lrtOracle.rsETHPrice()` is a **stored state variable** that is only refreshed when `updateRSETHPrice()` is called. [2](#0-1) 

`updateRSETHPrice()` is a public function but is **never called atomically inside `depositETH()` or `depositAsset()`**: [3](#0-2) 

The stored price is computed as:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
``` [4](#0-3) 

When underlying LSTs appreciate (e.g., stETH accrues staking rewards), `totalETHInProtocol` rises immediately, but `rsETHPrice` remains at its last stored value. During this gap, the denominator in `getRsETHAmountToMint()` is **artificially low**, so the depositor receives more rsETH than the true exchange rate warrants. The excess rsETH dilutes every existing holder's proportional claim on the pool.

The same stale price is used in `LRTWithdrawalManager.getExpectedAssetAmount()`:

```
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)
``` [5](#0-4) 

If `rsETHPrice` is stale-high (underlying assets have depreciated but the stored price has not yet been updated), a caller of `instantWithdrawal()` receives more assets than they are owed. [6](#0-5) 

An additional amplifier exists: `_updateRsETHPrice()` reverts for non-managers when the price increase exceeds `pricePercentageLimit`, which can **block** the price update entirely and extend the stale window indefinitely. [7](#0-6) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders accumulate yield as underlying LSTs appreciate. A depositor who acts while `rsETHPrice` is stale captures a portion of that accrued yield by receiving excess rsETH. After `updateRSETHPrice()` is called, the new price is lower than it would have been without the dilutive deposit, permanently reducing the value of every pre-existing rsETH token. The attack is repeatable at every price-update cycle and scales linearly with deposit size.

---

### Likelihood Explanation

`updateRSETHPrice()` is not called inside any deposit or withdrawal path. There is always a non-zero lag between LST yield accrual and the next price update. No privileged access is required; any depositor can observe on-chain that `rsETHPrice` is stale (by comparing `lrtOracle.rsETHPrice()` against a freshly computed TVL / supply ratio) and act without front-running any specific transaction. The `pricePercentageLimit` guard can further widen the window by blocking updates when appreciation is large.

---

### Recommendation

1. **Call `_updateRsETHPrice()` atomically at the start of `depositETH()`, `depositAsset()`, and `instantWithdrawal()`** before computing the exchange rate, so the mint/burn always uses a fresh price.
2. Alternatively, derive the rsETH amount directly from the live TVL and current supply rather than from the cached `rsETHPrice` state variable.
3. If the `pricePercentageLimit` guard is intended to throttle large single-step increases, ensure it does not permanently block updates; consider a time-based override.

---

### Proof of Concept

**Setup:**
- `totalETHInProtocol` = 1 010 ETH (stETH appreciated 1 %)
- `rsethSupply` = 1 000 rsETH
- `rsETHPrice` (stale) = 1.000 ETH (true value = 1.010 ETH)

**Attack:**

1. Attacker calls `depositETH{value: 100 ether}(0, "")` before `updateRSETHPrice()` is called.
2. `getRsETHAmountToMint` returns `100e18 * 1e18 / 1.000e18 = 100 rsETH`.  
   Correct amount would be `100e18 * 1e18 / 1.010e18 ≈ 99.01 rsETH`.  
   Attacker receives **~0.99 rsETH in excess**.
3. After deposit: `totalETH = 1 110`, `rsethSupply = 1 100`, true price = `1 110 / 1 100 ≈ 1.009 ETH`.
4. `updateRSETHPrice()` is called; `rsETHPrice` is set to 1.009 ETH.
5. Attacker's 100 rsETH is now worth `100 × 1.009 = 100.9 ETH` — a **0.9 ETH profit on a 100 ETH deposit**.
6. Existing 1 000 rsETH holders collectively hold `1 000 × 1.009 = 1 009 ETH` instead of the `1 010 ETH` they were entitled to — a **1 ETH loss** extracted by the attacker.
7. The attack is repeatable at every price-update interval, compounding dilution over time.

### Citations

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L28-29)
```text
    uint256 public override rsETHPrice;
    uint256 public pricePercentageLimit;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
