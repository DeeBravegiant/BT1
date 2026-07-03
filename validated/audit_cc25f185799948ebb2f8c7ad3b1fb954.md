### Title
Single Depositor Can Exhaust Global Daily Mint Limit, DoS-ing All Subsequent Deposits for Up to 24 Hours - (File: contracts/RSETH.sol)

---

### Summary

The `RSETH.sol` contract enforces a **shared global** daily mint limit via the `checkDailyMintLimit` modifier. A single unprivileged depositor can consume the entire `maxMintAmountPerDay` quota in one transaction, causing every subsequent `RSETH.mint()` call to revert with `DailyMintLimitExceeded` for up to 24 hours, effectively DoS-ing all other depositors.

---

### Finding Description

`RSETH.mint()` is gated by the `checkDailyMintLimit` modifier:

```solidity
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
    }
    currentPeriodMintedAmount += amount;
    _;
}
``` [1](#0-0) 

`currentPeriodMintedAmount` is a **single shared counter** across all depositors. There is no per-user sub-limit. Any caller who deposits enough to push `currentPeriodMintedAmount` to `maxMintAmountPerDay` will exhaust the quota for the entire period.

The deposit entry path is fully public and permissionless:

1. Attacker calls `LRTDepositPool.depositETH{value: X}()` (or `depositAsset`) where `X` is sized to consume the remaining daily quota.
2. `LRTDepositPool._beforeDeposit()` computes `rsethAmountToMint` and calls `_mintRsETH()`.
3. `_mintRsETH()` calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)`.
4. `RSETH.mint()` applies `checkDailyMintLimit(rsethAmountToMint)`, which increments `currentPeriodMintedAmount` to `maxMintAmountPerDay`.
5. Every subsequent `RSETH.mint()` call reverts with `DailyMintLimitExceeded` until the 24-hour window resets. [2](#0-1) [3](#0-2) [4](#0-3) 

The attacker receives rsETH proportional to their deposit and does not permanently lose funds; they simply lock capital for the duration of the EigenLayer withdrawal queue (7+ days). The DoS window is up to 24 hours per attack cycle, and the attack can be repeated at the start of each new period.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds.**

All honest depositors are blocked from depositing ETH or LSTs into the protocol for up to 24 hours per attack cycle. The `LRTDepositPool.depositETH()` and `depositAsset()` functions both revert because they ultimately call `RSETH.mint()`, which is gated by the exhausted daily limit. This is a temporary but complete denial of the deposit service for all users.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attacker must supply capital equal to the remaining daily quota (denominated in ETH/LST). They receive rsETH in return, so the net cost is only the opportunity cost of capital locked in the EigenLayer withdrawal queue. A well-capitalised actor (e.g., a competing protocol, a large holder, or a griefing party) can execute this repeatedly at the start of each 24-hour window with no permanent financial loss. No special role or privileged access is required — only a standard `depositETH` call.

---

### Recommendation

1. **Per-user sub-limits**: Track how much each address has minted within the current period and cap individual contributions to a fraction of `maxMintAmountPerDay`.
2. **Minimum deposit cooldown**: Introduce a per-address cooldown between large deposits to prevent rapid quota exhaustion.
3. **Proportional quota**: Allow each depositor to consume at most a configurable percentage of the daily limit per transaction.

---

### Proof of Concept

```
Precondition: maxMintAmountPerDay = D (e.g., 1000 ETH worth of rsETH)
              currentPeriodMintedAmount = 0 (fresh period)

Step 1: Attacker calls LRTDepositPool.depositETH{value: V}(0, "")
        where V is the ETH amount that yields rsETHAmount ≈ D.

Step 2: RSETH.mint() runs checkDailyMintLimit(D):
        currentPeriodMintedAmount (0) + D == maxMintAmountPerDay → passes.
        currentPeriodMintedAmount is now D.

Step 3: Honest user calls LRTDepositPool.depositETH{value: 1 ether}(0, "").
        RSETH.mint() runs checkDailyMintLimit(rsethForUser):
        currentPeriodMintedAmount (D) + rsethForUser > maxMintAmountPerDay → REVERT DailyMintLimitExceeded.

Step 4: All deposits revert until block.timestamp >= periodStartTime + 1 days.
        Attacker repeats at the start of the next period.
``` [1](#0-0) [5](#0-4)

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

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L684-690)
```text
    /// @dev private function to mint rseth
    /// @param rsethAmountToMint Amount of rseth minted
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
