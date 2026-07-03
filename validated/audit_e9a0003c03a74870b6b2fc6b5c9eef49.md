### Title
ETH Donation to `LRTDepositPool` Inflates `rsETHPrice`, Enabling Theft of Depositor Funds - (File: contracts/LRTDepositPool.sol / contracts/LRTOracle.sol)

---

### Summary

The `LRTDepositPool` contract's `receive()` function accepts ETH from any caller with no access control. The `getETHDistributionData()` function counts the raw `address(this).balance` of the deposit pool as protocol TVL. The public `updateRSETHPrice()` function in `LRTOracle` recomputes the cached `rsETHPrice` as `totalETHInProtocol / rsethSupply`. When `pricePercentageLimit` is zero (the default — it is never set in `initialize()`), there is no cap on how much the price can increase in a single update. An attacker can exploit this to inflate `rsETHPrice` to an astronomically large value, causing subsequent depositors to receive zero rsETH for their ETH, while the attacker redeems their rsETH at the inflated price to claim both the donation and the victims' deposits.

---

### Finding Description

**Root cause 1 — Unguarded ETH balance counted as TVL:** [1](#0-0) 

The `receive()` function accepts ETH from any address. `getETHDistributionData()` then counts the entire raw balance: [2](#0-1) 

This means a direct ETH donation inflates `totalETHInProtocol` used by the oracle.

**Root cause 2 — Public price update with no default rate-limit:** [3](#0-2) 

`updateRSETHPrice()` is callable by anyone. The price-increase guard is: [4](#0-3) 

Because `pricePercentageLimit` is never set in `initialize()` and defaults to `0`, the condition `pricePercentageLimit > 0` is always `false`, so `isPriceIncreaseOffLimit` is always `false`. There is no cap on the price increase.

**Root cause 3 — rsETH minting uses the cached (now inflated) price:** [5](#0-4) 

`rsethAmountToMint = (amount * assetPrice) / rsETHPrice`. When `rsETHPrice` is inflated to an enormous value, this division truncates to zero for any realistic deposit amount.

**Root cause 4 — Zero-rsETH mint is not rejected:** [6](#0-5) 

The only guard is `rsethAmountToMint < minRSETHAmountExpected`. If the caller passes `minRSETHAmountExpected = 0` (the common default), a zero-rsETH mint is silently accepted and the depositor's ETH is absorbed into the pool with no shares issued.

---

### Impact Explanation

**Critical — Direct theft of user funds.**

Concrete numbers (all values in wei):

| Step | rsETHPrice | rsethSupply | Pool ETH balance |
|---|---|---|---|
| Initial | 1e18 | 0 | 0 |
| Attacker deposits 1 wei ETH | 1e18 | 1 | 1 |
| Attacker donates 1000 ETH | 1e18 (stale) | 1 | 1000e18 + 1 |
| `updateRSETHPrice()` called | ≈ 1000e36 | 1 | 1000e18 + 1 |
| Victim deposits 1 ETH | ≈ 1000e36 | 1 (unchanged) | 1001e18 + 1 |
| `updateRSETHPrice()` called | ≈ 1001e36 | 1 | 1001e18 + 1 |
| Attacker withdraws 1 wei rsETH | — | — | claims ≈ 1001 ETH |

The attacker's net gain is the victim's 1 ETH. The attack

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```
