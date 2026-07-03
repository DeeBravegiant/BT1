### Title
`remainingDailyMintLimit()` Underflow and Deposit Freeze When Admin Reduces `dailyMintLimit` Below Current `dailyMintAmount` - (File: contracts/pools/RSETHPoolV2.sol, contracts/pools/RSETHPoolV3.sol)

---

### Summary

In `RSETHPoolV2.sol` and `RSETHPoolV3.sol`, the `remainingDailyMintLimit()` view function performs an unchecked subtraction `dailyMintLimit - effectiveDailyMintAmount`. When the admin calls `setDailyMintLimit()` with a value lower than the already-accumulated `dailyMintAmount` for the current day, this subtraction underflows and reverts. More critically, the `limitDailyMint` modifier — which gates every `deposit()` call — also evaluates `dailyMintAmount + rsETHAmount > dailyMintLimit`, which is always `true` in this state, causing every deposit to revert with `DailyMintLimitExceeded` until the next day resets `dailyMintAmount` to zero.

---

### Finding Description

`RSETHPoolV2.sol` exposes `setDailyMintLimit()` to the `DEFAULT_ADMIN_ROLE`:

```solidity
function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_dailyMintLimit == 0) {
        revert InvalidDailyMintLimit();
    }
    dailyMintLimit = _dailyMintLimit;
    emit DailyMintLimitSet(_dailyMintLimit);
}
``` [1](#0-0) 

There is no check that the new `_dailyMintLimit` is ≥ the current `dailyMintAmount`. If the admin sets `dailyMintLimit` to a value below the amount already minted today, two things break:

**1. `remainingDailyMintLimit()` reverts with underflow:**

```solidity
function remainingDailyMintLimit() external view returns (uint256) {
    uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;
    return dailyMintLimit - effectiveDailyMintAmount; // underflows if dailyMintLimit < dailyMintAmount
}
``` [2](#0-1) 

The same pattern exists in `RSETHPoolV3.sol`: [3](#0-2) 

**2. All `deposit()` calls revert via `limitDailyMint`:**

```solidity
modifier limitDailyMint(uint256 amount) {
    ...
    if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
        revert DailyMintLimitExceeded();
    }
    ...
}
``` [4](#0-3) 

When `dailyMintLimit < dailyMintAmount`, the condition `dailyMintAmount + rsETHAmount > dailyMintLimit` is always `true`, so every deposit reverts until the next day resets `dailyMintAmount = 0`. [5](#0-4) 

Note: `RSETH.sol`'s `remainingDailyMintLimit()` correctly guards against this with `maxMintAmountPerDay > effectiveDailyMintAmount ? ... : 0`, but the pool contracts do not apply the same pattern. [6](#0-5) 

---

### Impact Explanation

All ETH deposits via `RSETHPoolV2.deposit()` and `RSETHPoolV3.deposit()` are blocked for the remainder of the current 24-hour window — up to ~24 hours. Users cannot swap ETH for wrsETH through these L2 pool contracts. This constitutes a **temporary freezing of funds** (medium severity).

---

### Likelihood Explanation

The admin legitimately reducing the daily mint limit is a routine operational action (e.g., responding to a security event or recalibrating limits). No malicious intent is required. The only precondition is that some minting has already occurred in the current day before the limit is lowered. This is a realistic scenario during normal protocol operation.

---

### Recommendation

In `setDailyMintLimit()`, add a check that the new limit is not below the current day's already-minted amount:

```solidity
function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_dailyMintLimit == 0) revert InvalidDailyMintLimit();
    uint256 effectiveAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;
    if (_dailyMintLimit < effectiveAmount) revert InvalidDailyMintLimit();
    dailyMintLimit = _dailyMintLimit;
    emit DailyMintLimitSet(_dailyMintLimit);
}
```

Also fix `remainingDailyMintLimit()` to return `0` instead of reverting, mirroring the safe pattern already used in `RSETH.sol`:

```solidity
return dailyMintLimit > effectiveDailyMintAmount ? dailyMintLimit - effectiveDailyMintAmount : 0;
```

---

### Proof of Concept

1. On day D, users deposit ETH via `RSETHPoolV2.deposit()`. `dailyMintAmount` accumulates to `X` (e.g., 100 rsETH). `dailyMintLimit` is currently `200`.
2. Admin calls `setDailyMintLimit(50)` — a valid non-zero value, accepted without revert.
3. Now `dailyMintLimit = 50 < dailyMintAmount = 100`.
4. Any subsequent call to `deposit()` enters `limitDailyMint`, evaluates `100 + rsETHAmount > 50` → `true` → reverts with `DailyMintLimitExceeded`.
5. Any call to `remainingDailyMintLimit()` evaluates `50 - 100` → Solidity 0.8 underflow panic revert.
6. All deposits are frozen until the next day when `dailyMintAmount` resets to `0`.

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L72-94)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L252-257)
```text
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L357-363)
```text
    function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dailyMintLimit == 0) {
            revert InvalidDailyMintLimit();
        }
        dailyMintLimit = _dailyMintLimit;
        emit DailyMintLimitSet(_dailyMintLimit);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L345-350)
```text
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }
```

**File:** contracts/RSETH.sol (L265-272)
```text
    function remainingDailyMintLimit() external view returns (uint256) {
        if (maxMintAmountPerDay == 0) return 0;

        // If we're on a new day but no mint has occurred yet, treat currentPeriodMintedAmount as 0
        uint256 effectiveDailyMintAmount = (block.timestamp >= periodStartTime + 1 days) ? 0 : currentPeriodMintedAmount;

        return maxMintAmountPerDay > effectiveDailyMintAmount ? maxMintAmountPerDay - effectiveDailyMintAmount : 0;
    }
```
