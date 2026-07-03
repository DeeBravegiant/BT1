### Title
Changing `rsETH` address via `LRTConfig::setRSETH` permanently freezes user funds in `LRTWithdrawalManager` - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTConfig::setRSETH` allows the admin to replace the `rsETH` token address at any time. `LRTWithdrawalManager` reads `lrtConfig.rsETH()` dynamically in both `initiateWithdrawal` (to receive rsETH from users) and `unlockQueue` (to burn rsETH). After the address is changed, the withdrawal manager holds OLD rsETH tokens but attempts to burn from the NEW rsETH contract, causing all pending withdrawal requests to be permanently frozen with no recovery path.

### Finding Description

`LRTConfig` exposes a setter that replaces the canonical rsETH token address: [1](#0-0) 

This address is consumed dynamically by `LRTWithdrawalManager`. During `initiateWithdrawal`, the contract pulls rsETH from the user using the **current** `lrtConfig.rsETH()`: [2](#0-1) 

Later, during `unlockQueue`, the contract attempts to burn rsETH it holds using the **current** `lrtConfig.rsETH()`: [3](#0-2) 

If `setRSETH` is called between these two operations:

1. The withdrawal manager holds OLD rsETH tokens (transferred in during `initiateWithdrawal`).
2. `unlockQueue` calls `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)` against the NEW rsETH contract.
3. The withdrawal manager holds zero NEW rsETH, so `burnFrom` reverts.
4. The entire withdrawal queue for all assets is permanently blocked.
5. Users' OLD rsETH tokens are irrecoverably stuck — there is no `recoverERC20` or equivalent sweep function that can return them.

The `instantWithdrawal` path is similarly broken: it calls `IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked)` against the new address, which will revert for any user who holds only OLD rsETH. [4](#0-3) 

### Impact Explanation

**Critical — Permanent freezing of funds.**

All rsETH tokens deposited into `LRTWithdrawalManager` via `initiateWithdrawal` before the `setRSETH` call become permanently unrecoverable. The withdrawal queue cannot be processed (`unlockQueue` reverts), `completeWithdrawal` can never be reached, and no sweep/recovery function exists in the contract. Users lose their rsETH with no on-chain remedy.

### Likelihood Explanation

Low-to-medium. The admin legitimately holds `DEFAULT_ADMIN_ROLE` and `setRSETH` is a documented setter intended for protocol upgrades (e.g., deploying a new rsETH implementation). The protocol cannot guarantee that all `initiateWithdrawal` calls are settled before a `setRSETH` call, especially since withdrawal requests have an 8-day delay (`withdrawalDelayBlocks`). Any rsETH upgrade during that window silently freezes all in-flight withdrawals. No mempool ordering guarantee can prevent this race.

### Recommendation

Store the rsETH address at the time of `initiateWithdrawal` inside the `WithdrawalRequest` struct, and use that stored address in `unlockQueue` and `completeWithdrawal` instead of re-reading `lrtConfig.rsETH()` at execution time. Alternatively, add a migration function that allows the withdrawal manager to swap OLD rsETH for NEW rsETH atomically when `setRSETH` is called, ensuring no in-flight requests are stranded.

### Proof of Concept

1. User A calls `initiateWithdrawal(stETH, 100e18)`. The withdrawal manager receives 100 OLD rsETH via `IERC20(lrtConfig.rsETH()).safeTransferFrom(...)`.
2. Admin calls `LRTConfig::setRSETH(newRsETH)`, replacing the rsETH address.
3. Operator calls `unlockQueue(stETH, ...)`. Internally it computes `rsETHBurned = 100e18` and calls `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), 100e18)` — this targets NEW rsETH.
4. The withdrawal manager holds 0 NEW rsETH → `burnFrom` reverts with insufficient balance.
5. User A's 100 OLD rsETH is permanently locked in `LRTWithdrawalManager` with no recovery path. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTConfig.sol (L215-219)
```text
    function setRSETH(address rsETH_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(rsETH_);
        rsETH = rsETH_;
        emit SetRSETH(rsETH_);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L162-177)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L229-229)
```text
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L299-307)
```text
        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```
