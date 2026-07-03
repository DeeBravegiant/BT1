### Title
Daily Mint Limit Bypassable at Day Boundary via Two-Block Exploit - (File: `contracts/pools/RSETHPoolV3.sol`, `RSETHPoolV2.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

The `limitDailyMint` modifier across all L2 pool contracts uses integer division of `block.timestamp` to compute the current "day." Because this division truncates at a fixed boundary, an attacker can mint up to **2× the `dailyMintLimit`** in just two transactions — one at the last block of day X and one at the first block of day X+1 — bypassing the intended daily cap entirely.

---

### Finding Description

All four L2 pool variants implement a `getCurrentDay()` function:

```solidity
function getCurrentDay() public view returns (uint256) {
    return (block.timestamp - startTimestamp) / 1 days;
}
``` [1](#0-0) 

The `limitDailyMint` modifier uses this to reset `dailyMintAmount` when a new day is detected:

```solidity
uint256 currentDay = getCurrentDay();
if (currentDay > lastMintDay) {
    lastMintDay = currentDay;
    dailyMintAmount = 0;
}
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
``` [2](#0-1) 

Because `(block.timestamp - startTimestamp) / 1 days` uses integer truncation, the "day" increments the moment `block.timestamp - startTimestamp` crosses any multiple of `86400`. An attacker can:

1. **Block N** (e.g., `23:59:59` of day X): Call `deposit()` with the maximum amount allowed by `dailyMintLimit`. `dailyMintAmount` is now at the cap.
2. **Block N+1** (e.g., `00:00:01` of day X+1): Call `deposit()` again. `currentDay > lastMintDay` is now true, so `dailyMintAmount` resets to `0`, and another full `dailyMintLimit` worth of wrsETH is minted.

Net result: **2× `dailyMintLimit`** minted in two consecutive blocks, with the daily cap providing zero protection at the boundary.

The same vulnerable pattern is identically replicated in:
- `RSETHPoolV2.sol` — `limitDailyMint` modifier [3](#0-2) 
- `RSETHPoolV3ExternalBridge.sol` — `limitDailyMint` modifier [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.sol` — `limitDailyMint` modifier [5](#0-4) 

---

### Impact Explanation

The `dailyMintLimit` is the protocol's primary rate-limiting security control on L2 pools. Its purpose is to cap the amount of wrsETH minted per day, limiting exposure to oracle rate drift, flash-loan-assisted deposit attacks, or any scenario where minting at scale is harmful. By minting 2× the intended daily cap in two blocks, an attacker defeats this control entirely. The protocol fails to deliver the promised rate-limiting guarantee, and any downstream security assumption that relies on the daily cap being respected is violated.

**Impact: Low — Contract fails to deliver promised returns** (the daily mint limit security invariant is broken at every day boundary).

---

### Likelihood Explanation

This requires no special privileges, no oracle manipulation, and no external protocol compromise. Any depositor with sufficient capital can execute this by simply timing two `deposit()` calls across a day boundary. The day boundary occurs predictably every 86400 seconds. Likelihood is **High** — the condition is deterministic and requires only capital and timing.

---

### Recommendation

Track elapsed time in seconds rather than discrete day buckets, using a rolling window. Replace the truncation-based day comparison with a timestamp-based window:

```solidity
uint256 public windowStart;

modifier limitDailyMint(uint256 amount, address token) {
    // ...compute rsETHAmount...

    if (block.timestamp >= windowStart + 1 days) {
        windowStart = block.timestamp;
        dailyMintAmount = 0;
    }

    if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
        revert DailyMintLimitExceeded();
    }
    dailyMintAmount += rsETHAmount;
    _;
}
```

This ensures the window is always a full 86400 seconds from the last reset, eliminating the boundary-crossing exploit.

---

### Proof of Concept

Assume `dailyMintLimit = 100 ether` worth of wrsETH and `startTimestamp = 0`.

- At `block.timestamp = 86399` (last second of day 1): `getCurrentDay()` returns `0`. Attacker deposits to mint exactly `100 ether` wrsETH. `dailyMintAmount = 100 ether`, `lastMintDay = 0`.
- At `block.timestamp = 86400` (first second of day 2): `getCurrentDay()` returns `1`. Since `1 > 0`, `dailyMintAmount` resets to `0`. Attacker deposits again to mint another `100 ether` wrsETH.

Total minted: **200 ether** wrsETH in two consecutive blocks, against a stated daily cap of `100 ether`. [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

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

**File:** contracts/pools/RSETHPoolV3.sol (L339-341)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }
```

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L130-159)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L108-137)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

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
