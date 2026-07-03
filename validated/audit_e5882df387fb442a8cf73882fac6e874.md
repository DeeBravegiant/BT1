### Title
Rebasing stETH Token Amounts Stored as Fixed Values in Withdrawal Queue Lead to Insolvency and Temporary Fund Freeze - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager` supports stETH as a withdrawal asset and stores fixed token amounts in `request.expectedAssetAmount` after `unlockQueue()` is called. Because stETH is a rebasing token whose balance changes autonomously, a negative rebase between `unlockQueue()` and `completeWithdrawal()` causes the contract to hold less stETH than the sum of all committed `expectedAssetAmount` values, resulting in a first-come-first-served race where late claimers are permanently blocked.

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` has three stages:

**Stage 1 – `initiateWithdrawal()`**: The user's `rsETHUnstaked` is locked and a fixed `expectedAssetAmount` is computed from oracle prices and stored in `withdrawalRequests`, while `assetsCommitted[asset]` is incremented by the same fixed amount. [1](#0-0) 

**Stage 2 – `unlockQueue()`**: The operator calls this function, which reads the actual stETH balance from `LRTUnstakingVault` (`unstakingVault.balanceOf(asset)`), recalculates each request's payout, and then calls `unstakingVault.redeem(asset, assetAmountUnlocked)` to transfer the exact stETH amount needed into `LRTWithdrawalManager`. [2](#0-1) 

The per-request `expectedAssetAmount` is overwritten with the fixed payout amount at this point: [3](#0-2) 

**Stage 3 – `completeWithdrawal()`**: The user calls this, and the contract transfers exactly `request.expectedAssetAmount` stETH to the user via `IERC20.safeTransfer`. [4](#0-3) 

**Root cause**: After Stage 2, the `LRTWithdrawalManager` holds a fixed stETH token balance equal to the sum of all unlocked `expectedAssetAmount` values. stETH is a rebasing token — its balance changes autonomously without any transfer. If a negative rebase (e.g., validator slashing) occurs between Stage 2 and Stage 3, the contract's actual stETH balance decreases below the sum of committed amounts. The contract has no mechanism to detect or handle this discrepancy.

The `_transferAsset` function uses a plain `IERC20.safeTransfer` with no share-based accounting: [5](#0-4) 

Additionally, `assetsCommitted[asset]` is tracked in token amounts (not shares), so between Stage 1 and Stage 2, a negative rebase causes `assetsCommitted` to exceed the actual vault balance, making `getAvailableAssetAmount()` return 0 and blocking all new withdrawals: [6](#0-5) 

### Impact Explanation

**Temporary freeze of funds (Medium)**: After `unlockQueue()` transfers stETH into `LRTWithdrawalManager`, a negative rebase reduces the contract's stETH balance. Users who call `completeWithdrawal()` first drain the available balance; subsequent users' calls revert because `safeTransfer` fails when the contract lacks sufficient stETH. Their rsETH has already been burned (at `unlockQueue()` time via `IRSETH.burnFrom`) and cannot be recovered. [7](#0-6) 

**Unfair distribution**: Early claimers receive their full `expectedAssetAmount`; late claimers receive nothing, even though all users experienced the same slashing event. The loss is concentrated on the last users to claim rather than being shared proportionally.

**Protocol insolvency risk**: If the rebase is severe enough that the vault cannot cover all committed withdrawals even after `unlockQueue()` is re-run, rsETH becomes undercollateralized — the burned rsETH supply no longer corresponds to recoverable assets.

### Likelihood Explanation

stETH is an explicitly supported and actively used asset in the protocol, as evidenced by the `ST_ETH_TOKEN` constant, the `initialize2` function seeding `unlockedWithdrawalsCount` for stETH, and the `LRTConverter` containing dedicated stETH unstaking logic. [8](#0-7) 

The withdrawal delay is 8 days, creating a substantial window during which stETH sits in `LRTUnstakingVault` with `assetsCommitted` tracking a fixed amount. After unlock, stETH sits in `LRTWithdrawalManager` until each user individually calls `completeWithdrawal()`. Ethereum validator slashing events, while infrequent, are realistic and have occurred historically. Any slashing event during either window triggers the vulnerability.

### Recommendation

Store and transfer stETH in **shares** rather than token amounts throughout the withdrawal lifecycle:

1. In `initiateWithdrawal()`, convert `expectedAssetAmount` to stETH shares using `IStETH(stETH).getSharesByPooledEth(expectedAssetAmount)` and store the shares value.
2. In `_unlockWithdrawalRequests()`, compute `payoutAmount` in shares and store shares in `request.expectedAssetAmount`.
3. In `_processWithdrawalCompletion()`, use `IStETH(stETH).transferShares(user, sharesAmount)` instead of `IERC20.safeTransfer`.
4. Update `assetsCommitted[stETH]` to track shares, and convert to token amounts only when computing `getAvailableAssetAmount()` using `IStETH.getPooledEthByShares()`.

This ensures that all accounting is rebase-invariant and each user receives their proportional share of the stETH balance regardless of rebasing events between initiation and claim.

### Proof of Concept

1. Alice and Bob each initiate stETH withdrawals via `initiateWithdrawal()`. Their `expectedAssetAmount` values (e.g., 90 stETH and 10 stETH) are stored as fixed amounts and `assetsCommitted[stETH] = 100e18`.
2. The operator calls `unlockQueue()` after the 8-day delay. The vault's 100 stETH is transferred to `LRTWithdrawalManager`. Both requests are unlocked with `expectedAssetAmount` = 90 and 10 stETH respectively. rsETH is burned for both.
3. A validator slashing event causes a 10% negative rebase. `LRTWithdrawalManager` now holds 90 stETH (was 100).
4. Alice calls `completeWithdrawal()` and receives 90 stETH — her full amount, draining the contract.
5. Bob calls `completeWithdrawal()` and the transaction reverts: the contract holds 0 stETH but owes Bob 10 stETH. Bob's rsETH is already burned and unrecoverable.
6. The protocol has burned 100 rsETH but only delivered 90 stETH worth of value, leaving rsETH undercollateralized.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L118-118)
```text
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
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

**File:** contracts/LRTWithdrawalManager.sol (L802-804)
```text
            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
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
