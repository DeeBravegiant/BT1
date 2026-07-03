### Title
Stale `rsETHPrice` Used in Deposit Mint Calculation Without Prior Oracle Update — (File: `contracts/LRTDepositPool.sol`)

---

### Summary
`LRTDepositPool.getRsETHAmountToMint()` reads the cached `LRTOracle.rsETHPrice` state variable directly without first calling `LRTOracle.updateRSETHPrice()`. Because `rsETHPrice` is a stored value that only updates on explicit invocation, any depositor who transacts while the price is stale receives more rsETH than they are entitled to, diluting existing holders' accrued yield.

---

### Finding Description
`LRTOracle` stores the rsETH/ETH exchange rate in the state variable `rsETHPrice`. [1](#0-0) 

This value is **not** computed on-the-fly; it is only refreshed when `updateRSETHPrice()` (public, permissionless) or `updateRSETHPriceAsManager()` (manager-only) is explicitly called. [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` uses this stored value directly to compute how many rsETH tokens to mint for a depositor:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

There is no call to `updateRSETHPrice()` before this division. If `rsETHPrice` has not been refreshed since the last staking reward accrual, it is lower than the true current value. A lower denominator inflates `rsethAmountToMint`, giving the depositor more rsETH than their deposit warrants.

---

### Impact Explanation
`rsETHPrice` monotonically increases over time as EigenLayer staking rewards accrue to the protocol TVL. When a deposit is processed against a stale (lower) `rsETHPrice`, the new depositor receives excess rsETH. Because rsETH is a yield-bearing token whose value is backed by the total protocol TVL divided by total supply, issuing excess rsETH dilutes the ETH-per-rsETH ratio for all existing holders. The yield that existing holders had earned (but not yet redeemed) is effectively transferred to the new depositor.

**Impact: High — Theft of unclaimed yield from existing rsETH holders.**

---

### Likelihood Explanation
`updateRSETHPrice()` is public and permissionless, so it is expected to be called by off-chain keepers. However:

1. The protocol enforces **no on-chain freshness requirement** before minting.
2. During periods of low keeper activity (network congestion, keeper downtime, or deliberate griefing of the keeper), `rsETHPrice` can lag behind the true value.
3. An attacker can passively monitor the staleness of `rsETHPrice` (by comparing it against the live TVL calculation) and time a large deposit to the window where the price has not been updated, maximising the excess rsETH received.

**Likelihood: Medium** — requires no special privilege; only timing awareness.

---

### Recommendation
Call `updateRSETHPrice()` (or an internal equivalent) at the start of `depositAsset()` before computing the mint amount, analogous to how the Radiant fix required triggering the oracle `update` before querying price data. Alternatively, enforce a maximum staleness window on `rsETHPrice` and revert deposits if the price has not been refreshed within that window.

---

### Proof of Concept

1. At time T, `rsETHPrice = 1.01e18` (last updated).
2. Staking rewards accrue; true rsETH price rises to `1.02e18`, but `updateRSETHPrice()` has not been called.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, ...)`.
4. `getRsETHAmountToMint` computes: `(100e18 * 1e18) / 1.01e18 ≈ 99.0099 rsETH` instead of the correct `(100e18 * 1e18) / 1.02e18 ≈ 98.0392 rsETH`.
5. Attacker receives ~0.97 excess rsETH per 100 ETH deposited, at the expense of existing holders whose rsETH is now worth proportionally less ETH.
6. Attacker redeems rsETH through the withdrawal pipeline, extracting the stolen yield.

The entry path is fully unprivileged: `depositAsset()` → `getRsETHAmountToMint()` → `lrtOracle.rsETHPrice()` (stale). [4](#0-3) [2](#0-1)

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

**File:** contracts/LRTDepositPool.sol (L511-521)
```text
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
