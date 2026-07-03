Audit Report

## Title
Rebasing stETH Token Amounts Stored as Fixed Values in Withdrawal Queue Lead to Insolvency and Permanent Fund Freeze - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager` stores fixed stETH token amounts in `request.expectedAssetAmount` after `unlockQueue()` transfers stETH into the contract. Because stETH is a rebasing token, a negative rebase between `unlockQueue()` and `completeWithdrawal()` reduces the contract's actual stETH balance below the sum of all committed amounts. Late claimants' `completeWithdrawal()` calls revert permanently, and because rsETH is burned at `unlockQueue()` time, affected users lose their funds with no recovery path.

## Finding Description

**Stage 1 – `initiateWithdrawal()`**: A fixed `expectedAssetAmount` is computed from oracle prices and stored in `withdrawalRequests`, while `assetsCommitted[asset]` is incremented by the same fixed token amount. [1](#0-0) 

**Stage 2 – `unlockQueue()`**: `_unlockWithdrawalRequests()` overwrites `request.expectedAssetAmount` with a fixed `payoutAmount` (token amount, not shares), decrements `assetsCommitted`, and then calls `unstakingVault.redeem(asset, assetAmountUnlocked)` to pull the exact stETH token amount into `LRTWithdrawalManager`. [2](#0-1) [3](#0-2) 

rsETH is burned at this point — before users claim: [4](#0-3) 

**Stage 3 – `completeWithdrawal()`**: The contract transfers exactly `request.expectedAssetAmount` stETH via a plain `IERC20.safeTransfer` with no share-based accounting: [5](#0-4) [6](#0-5) 

**Root cause**: `LRTUnstakingVault.balanceOf()` returns `IERC20(asset).balanceOf(address(this))` — a rebasing token amount, not shares: [7](#0-6) 

After Stage 2, `LRTWithdrawalManager` holds a fixed stETH token balance equal to the sum of all unlocked `expectedAssetAmount` values. A negative rebase (e.g., validator slashing) autonomously reduces this balance without any transfer. The contract has no mechanism to detect or handle this discrepancy. Early claimants drain the available balance; subsequent claimants' `safeTransfer` calls revert. Since rsETH was already burned at `unlockQueue()` time, affected users have no recovery path.

Notably, the `initiateWithdrawal()` NatSpec comment itself acknowledges the slashing edge case without providing a fix: [8](#0-7) 

The `assetsCommitted[asset]` tracking between Stage 1 and Stage 2 also uses token amounts, so a negative rebase during that window causes `getAvailableAssetAmount()` to return 0, blocking all new withdrawal initiations: [9](#0-8) 

## Impact Explanation

**Permanent freezing of funds (Critical)**: Late claimants' `completeWithdrawal()` calls revert because the contract's stETH balance is insufficient. Their rsETH has already been burned at `unlockQueue()` time and cannot be recovered. There is no mechanism to re-mint rsETH, compensate users, or force-complete withdrawals at a reduced amount. The `sweepRemainingAssets()` function cannot be called while `unlockedWithdrawalsCount[asset] > 0`, so the residual stETH is also locked.

**Protocol insolvency (Critical)**: The burned rsETH supply no longer corresponds to recoverable assets. The protocol has destroyed rsETH representing 100 stETH of value but can only deliver 90 stETH, leaving rsETH undercollateralized.

## Likelihood Explanation

stETH is an explicitly supported and actively used asset, confirmed by the `ST_ETH_TOKEN` constant, the `initialize2` function seeding `unlockedWithdrawalsCount` for stETH, and the `LRTConverter` containing dedicated stETH unstaking logic. [10](#0-9) 

The withdrawal delay is 8 days (`withdrawalDelayBlocks = 8 days / 12 seconds`), creating a substantial window during which stETH sits in `LRTUnstakingVault` with `assetsCommitted` tracking a fixed amount. [11](#0-10) 

After unlock, stETH sits in `LRTWithdrawalManager` until each user individually calls `completeWithdrawal()`, creating a second window. Ethereum validator slashing events are infrequent but historically documented and realistic. No privileged access is required — any normal user calling `completeWithdrawal()` after a negative rebase triggers the impact.

## Recommendation

Store and transfer stETH in **shares** rather than token amounts throughout the withdrawal lifecycle:

1. In `initiateWithdrawal()`, convert `expectedAssetAmount` to stETH shares using `IStETH(stETH).getSharesByPooledEth(expectedAssetAmount)` and store the shares value.
2. In `_unlockWithdrawalRequests()`, compute `payoutAmount` in shares and store shares in `request.expectedAssetAmount`.
3. In `_processWithdrawalCompletion()`, use `IStETH(stETH).transferShares(user, sharesAmount)` instead of `IERC20.safeTransfer`.
4. Update `assetsCommitted[stETH]` to track shares, and convert to token amounts only when computing `getAvailableAssetAmount()` using `IStETH.getPooledEthByShares()`.

This ensures all accounting is rebase-invariant and each user receives their proportional share of the stETH balance regardless of rebasing events between initiation and claim.

## Proof of Concept

1. Alice and Bob each call `initiateWithdrawal(stETH, ...)`. Their `expectedAssetAmount` values (90 stETH and 10 stETH) are stored as fixed token amounts; `assetsCommitted[stETH] = 100e18`.
2. After the 8-day delay, the operator calls `unlockQueue(stETH, ...)`. The vault's 100 stETH is transferred to `LRTWithdrawalManager`. Both requests are unlocked with `expectedAssetAmount` = 90e18 and 10e18 respectively. rsETH is burned for both via `IRSETH.burnFrom`.
3. A validator slashing event causes a 5% negative rebase. `LRTWithdrawalManager` now holds 95 stETH (was 100) — no transfer occurred, the balance changed autonomously.
4. Alice calls `completeWithdrawal()` and receives 90 stETH — her full amount, leaving 5 stETH in the contract.
5. Bob calls `completeWithdrawal()`. The `safeTransfer` of 10 stETH reverts: the contract holds only 5 stETH. Bob's rsETH is already burned and unrecoverable.
6. The protocol has burned rsETH representing 100 stETH of value but delivered only 90 stETH, leaving rsETH undercollateralized. Bob's 5 stETH residual is permanently locked because `hasUnlockedWithdrawals(stETH)` remains true (Bob's request was never completed), preventing `sweepRemainingAssets()` from being called.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L118-118)
```text
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
```

**File:** contracts/LRTWithdrawalManager.sol (L147-150)
```text
    /// @dev This function is only callable by the user and is used to initiate a withdrawal request for a specific
    /// asset. Will be finalised by calling `completeWithdrawal` after the manager unlocked the request and the delay
    /// has past. There is an edge case were the user withdraws last underlying asset and that asset gets slashed.
    function initiateWithdrawal(
```

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L301-307)
```text
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L800-808)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

```

**File:** contracts/LRTWithdrawalManager.sol (L876-882)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
```

**File:** contracts/LRTUnstakingVault.sol (L218-224)
```text
    function balanceOf(address asset) public view returns (uint256) {
        if (asset == LRTConstants.ETH_TOKEN) {
            return address(this).balance;
        } else {
            return IERC20(asset).balanceOf(address(this));
        }
    }
```
