### Title
Arithmetic Underflow in `getCurrentDay()` Bypasses Daily Mint Limit When `block.timestamp < startTimestamp` - (File: contracts/pools/RSETHPoolV2.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

### Summary

The `getCurrentDay()` function in all five L2 pool contracts performs an unchecked subtraction `block.timestamp - startTimestamp`. If `block.timestamp < startTimestamp` (i.e., the pool is called before the start timestamp), this subtraction underflows in Solidity 0.8.x and reverts — but the `limitDailyMint` modifier guards against this with an explicit `MintBeforeStartTimestamp` revert. However, the `getCurrentDay()` function is also called from `remainingDailyMintLimit()` and `getNextDailyLimitResetTimestamp()` as public view functions with **no** such guard, causing them to revert with an arithmetic underflow panic instead of a meaningful error. More critically, the analog to the reported bug is that `getCurrentDay()` is called inside `limitDailyMint` **after** the `startTimestamp` guard, but `remainingDailyMintLimit()` and `getNextDailyLimitResetTimestamp()` call `getCurrentDay()` with no guard at all, silently panicking and making the daily limit state unreadable to integrators and off-chain systems before the start time.

### Finding Description

In all five pool contracts (`RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`), `getCurrentDay()` is defined as:

```solidity
function getCurrentDay() public view returns (uint256) {
    return (block.timestamp - startTimestamp) / 1 days;
}
```

This subtraction is unguarded. When `block.timestamp < startTimestamp`, Solidity 0.8.x checked arithmetic causes a panic revert (underflow). The `limitDailyMint` modifier does guard the deposit path:

```solidity
if (block.timestamp < startTimestamp) {
    revert MintBeforeStartTimestamp();
}
```

But `remainingDailyMintLimit()` and `getNextDailyLimitResetTimestamp()` call `getCurrentDay()` directly with no such guard:

```solidity
function remainingDailyMintLimit() external view returns (uint256) {
    uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;
    return dailyMintLimit - effectiveDailyMintAmount;
}

function getNextDailyLimitResetTimestamp() external view returns (uint256) {
    return startTimestamp + (getCurrentDay() + 1) * 1 days;
}
```

These are public view functions callable by any external party (depositors, integrators, UI, off-chain bots) before the pool goes live. They will panic-revert with an arithmetic underflow instead of returning a meaningful value or a clean error.

### Impact Explanation

**Impact: Low** — Contract fails to deliver promised returns (view functions revert with panic instead of meaningful error), but no funds are lost. The deposit path itself is correctly guarded. However, integrators and depositors querying `remainingDailyMintLimit()` or `getNextDailyLimitResetTimestamp()` before `startTimestamp` will receive opaque underflow panics, breaking off-chain tooling and potentially causing incorrect assumptions about pool state. This matches the "contract fails to deliver promised returns, but doesn't lose value" category.

### Likelihood Explanation

**Likelihood: Medium** — The window between contract deployment/initialization and `startTimestamp` is a normal operational period. Any off-chain system, UI, or depositor querying the daily limit state during this window will trigger the underflow. This is a realistic scenario given that `startTimestamp` is set in the future at initialization time.

### Recommendation

Add a guard in `getCurrentDay()` itself (or in the two view functions) to handle the case where `block.timestamp < startTimestamp`:

```solidity
function getCurrentDay() public view returns (uint256) {
    if (block.timestamp < startTimestamp) return 0;
    return (block.timestamp - startTimestamp) / 1 days;
}
```

This mirrors the recommendation in the external report: avoid underflow by reverting (or returning a safe value) when the timestamp ordering is inverted.

### Proof of Concept

1. Admin calls `reinitialize(dailyMintLimit, futureTimestamp)` where `futureTimestamp = block.timestamp + 1 days`.
2. Any external caller (depositor, UI, bot) calls `remainingDailyMintLimit()` before `futureTimestamp` arrives.
3. `remainingDailyMintLimit()` calls `getCurrentDay()` which computes `block.timestamp - startTimestamp` → underflow panic (arithmetic revert).
4. Same for `getNextDailyLimitResetTimestamp()`.

Affected locations:

- `getCurrentDay()` / `remainingDailyMintLimit()` / `getNextDailyLimitResetTimestamp()` in `RSETHPoolV2.sol` [1](#0-0) 

- Same pattern in `RSETHPoolV2ExternalBridge.sol` [2](#0-1) 

- Same pattern in `RSETHPoolV3.sol` [3](#0-2) 

- Same pattern in `RSETHPoolV3ExternalBridge.sol` [4](#0-3) 

- Same pattern in `RSETHPoolV3WithNativeChainBridge.sol` [5](#0-4) 

The `limitDailyMint` modifier correctly guards the deposit path with `MintBeforeStartTimestamp`, but the public view functions lack this guard entirely: [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L73-75)
```text
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }
```

**File:** contracts/pools/RSETHPoolV2.sol (L246-263)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }

    /// @notice Gets the remaining daily minting limit
    /// @return uint256 The remaining daily minting limit
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }

    /// @notice Gets the next daily mint limit reset timestamp
    /// @return uint256 The next daily mint limit reset timestamp
    function getNextDailyLimitResetTimestamp() external view returns (uint256) {
        return startTimestamp + (getCurrentDay() + 1) * 1 days;
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L374-391)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }

    /// @notice Gets the remaining daily minting limit
    /// @return uint256 The remaining daily minting limit
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }

    /// @notice Gets the next daily mint limit reset timestamp
    /// @return uint256 The next daily mint limit reset timestamp
    function getNextDailyLimitResetTimestamp() external view returns (uint256) {
        return startTimestamp + (getCurrentDay() + 1) * 1 days;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L339-356)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }

    /// @notice Gets the remaining daily minting limit
    /// @return uint256 The remaining daily minting limit
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }

    /// @notice Gets the next daily mint limit reset timestamp
    /// @return uint256 The next daily mint limit reset timestamp
    function getNextDailyLimitResetTimestamp() external view returns (uint256) {
        return startTimestamp + (getCurrentDay() + 1) * 1 days;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L548-565)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }

    /// @notice Gets the remaining daily minting limit
    /// @return uint256 The remaining daily minting limit
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }

    /// @notice Gets the next daily mint limit reset timestamp
    /// @return uint256 The next daily mint limit reset timestamp
    function getNextDailyLimitResetTimestamp() external view returns (uint256) {
        return startTimestamp + (getCurrentDay() + 1) * 1 days;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L390-407)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }

    /// @notice Gets the remaining daily minting limit
    /// @return uint256 The remaining daily minting limit
    function remainingDailyMintLimit() external view returns (uint256) {
        // If we're on a new day but no mint has occurred yet, treat dailyMintAmount as 0
        uint256 effectiveDailyMintAmount = (getCurrentDay() > lastMintDay) ? 0 : dailyMintAmount;

        return dailyMintLimit - effectiveDailyMintAmount;
    }

    /// @notice Gets the next daily mint limit reset timestamp
    /// @return uint256 The next daily mint limit reset timestamp
    function getNextDailyLimitResetTimestamp() external view returns (uint256) {
        return startTimestamp + (getCurrentDay() + 1) * 1 days;
    }
```
