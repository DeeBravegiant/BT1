### Title
`burnFrom` Does Not Decrement `currentPeriodMintedAmount`, Causing Daily Mint Limit Accounting Mismatch — (File: contracts/RSETH.sol)

---

### Summary

`RSETH.mint` increments the `currentPeriodMintedAmount` accounting variable via the `checkDailyMintLimit` modifier, but `RSETH.burnFrom` never decrements it. This creates a persistent divergence between the tracked daily-minted amount and the actual circulating supply, which can cause legitimate deposits to be rejected for the remainder of a 24-hour window even when the real circulating supply is well below the configured limit.

---

### Finding Description

`RSETH.sol` maintains a daily mint-rate-limit enforced by the `checkDailyMintLimit` modifier: [1](#0-0) 

Every call to `mint` increments `currentPeriodMintedAmount` by the minted amount. The `burnFrom` function, however, only calls the OZ `_burn` primitive and performs no corresponding decrement: [2](#0-1) 

This is the direct analog of the external report's pattern: an operation that reduces a user's balance (and `totalSupply`) but leaves a separate accounting variable (`currentPeriodMintedAmount`) unchanged.

The `burnFrom` function is invoked by `LRTWithdrawalManager.instantWithdrawal`, which is callable by any unprivileged user: [3](#0-2) 

It is also invoked by `unlockQueue` when processing the withdrawal queue: [4](#0-3) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

`currentPeriodMintedAmount` becomes inflated relative to the actual circulating supply. Concretely:

1. Suppose `maxMintAmountPerDay = 1000 rsETH` and 800 rsETH has been minted → `currentPeriodMintedAmount = 800`.
2. A user calls `instantWithdrawal`, burning 500 rsETH. `currentPeriodMintedAmount` stays at 800.
3. A new depositor attempts to mint 300 rsETH. The modifier checks `800 + 300 > 1000` → reverts with `DailyMintLimitExceeded`.
4. The actual circulating supply is only 300 rsETH (800 − 500), far below the 1000-rsETH limit.

Deposits are blocked for the rest of the day despite the protocol having ample headroom. No funds are lost, but the protocol fails to deliver its core deposit service until the 24-hour window resets.

---

### Likelihood Explanation

Medium. `instantWithdrawal` is permissionless and callable by any rsETH holder. During periods of high withdrawal activity (e.g., a market downturn), many users burning rsETH within a single day will silently exhaust the daily mint capacity, blocking all new deposits until midnight UTC. The effect is amplified when `maxMintAmountPerDay` is set conservatively close to expected daily volume.

---

### Recommendation

Decrement `currentPeriodMintedAmount` inside `burnFrom`, capped at the current period's tracked amount to avoid underflow:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);

    // Reset period if needed (mirrors checkDailyMintLimit logic)
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }

    // Reduce the tracked minted amount so capacity is freed for new deposits
    if (currentPeriodMintedAmount >= amount) {
        currentPeriodMintedAmount -= amount;
    } else {
        currentPeriodMintedAmount = 0;
    }

    _burn(account, amount);
}
```

---

### Proof of Concept

```
// Scenario (values in ether)
maxMintAmountPerDay = 1000e18

Step 1: LRTDepositPool mints 800e18 rsETH for depositors
        → currentPeriodMintedAmount = 800e18

Step 2: User calls LRTWithdrawalManager.instantWithdrawal(ETH, 500e18, "")
        → RSETH.burnFrom(user, 500e18) is called
        → _burn reduces totalSupply by 500e18  ✓
        → currentPeriodMintedAmount remains 800e18  ✗ (BUG)

Step 3: New depositor calls LRTDepositPool.depositETH{value: 300e18}(...)
        → getRsETHAmountToMint returns ~300e18
        → RSETH.mint(depositor, 300e18) is called
        → checkDailyMintLimit: 800e18 + 300e18 = 1100e18 > 1000e18
        → REVERT: DailyMintLimitExceeded

Actual circulating supply: 300e18 (well below the 1000e18 limit)
Tracked currentPeriodMintedAmount: 800e18 (stale, inflated)
```

### Citations

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/RSETH.sol (L245-248)
```text
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L229-229)
```text
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```
