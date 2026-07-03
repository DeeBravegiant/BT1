### Title
Unprotected `sendFunds()` in `FeeReceiver` Enables MEV Reward Sniping — (File: contracts/FeeReceiver.sol)

---

### Summary

`FeeReceiver.sendFunds()` carries no access-control modifier, so any external caller can force the entire accumulated MEV/execution-layer reward balance into `LRTDepositPool` at will. Because the deposit pool's ETH balance is counted directly in TVL, this inflates the rsETH price on demand. An attacker can deposit ETH, trigger the reward flush, and immediately sell rsETH at the inflated price on a secondary market, capturing yield that was earned by long-term rsETH holders.

---

### Finding Description

`FeeReceiver` is the designated receiver of MEV and execution-layer rewards for the Kelp DAO protocol. Its `sendFunds()` function is the only mechanism to move those rewards into the deposit pool:

```solidity
// contracts/FeeReceiver.sol  line 53-58
function sendFunds() external {                          // ← no access control
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

There is no `onlyRole`, `onlyLRTManager`, or any other guard. Any EOA or contract can call it at any time.

The receiving side is equally open:

```solidity
// contracts/LRTDepositPool.sol  line 61
function receiveFromRewardReceiver() external payable { }
```

Once ETH lands in `LRTDepositPool`, it is immediately counted in TVL via `getETHDistributionData()`:

```solidity
// contracts/LRTDepositPool.sol  line 480
ethLyingInDepositPool = address(this).balance;
```

TVL feeds directly into the rsETH price used by `getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol  line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

An attacker who controls the moment `sendFunds()` is called therefore controls the moment the rsETH price jumps.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

MEV rewards accumulate in `FeeReceiver` over time and represent yield earned by all rsETH holders proportionally to their holding period. By calling `sendFunds()` immediately after depositing ETH (and before other users can react), an attacker receives a share of those rewards without having held rsETH during the period they were earned. The attacker can then exit via a secondary-market sale of rsETH at the inflated price, extracting value from existing holders.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. A meaningful ETH balance in `FeeReceiver` (routine after any validator proposal or MEV event).
2. A liquid secondary market for rsETH (exists on mainnet).
3. A single permissionless transaction to call `sendFunds()`.

No privileged access, no oracle manipulation, and no governance capture is needed. The entry path is fully open to any unprivileged caller.

---

### Recommendation

Add an access-control guard to `sendFunds()` so only an authorized role (e.g., `LRTConstants.MANAGER`) can trigger reward distribution:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Additionally, consider adding a caller whitelist to `LRTDepositPool.receiveFromRewardReceiver()` so that only the registered `FeeReceiver` contract can invoke it.

---

### Proof of Concept

Assume:
- `FeeReceiver` holds **100 ETH** in accumulated MEV rewards.
- `LRTDepositPool` TVL = **1 000 ETH**, rsETH supply = **1 000 rsETH** → price = **1.000 ETH/rsETH**.

**Step 1** — Attacker calls `LRTDepositPool.depositETH{value: 100 ETH}(minRSETH, "")`.  
Receives ≈ **100 rsETH** at price 1.000.

**Step 2** — Attacker calls `FeeReceiver.sendFunds()` (no guard, succeeds).  
100 ETH moves to `LRTDepositPool`. New TVL = **1 200 ETH**, supply = **1 100 rsETH** → price ≈ **1.0909 ETH/rsETH**.

**Step 3** — Attacker sells **100 rsETH** on a secondary market at ≈ 1.0909 ETH each → receives ≈ **109.09 ETH**.

**Net profit**: ≈ **9.09 ETH** extracted from MEV rewards that were earned by the 1 000 pre-existing rsETH holders over the reward accrual period. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
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
