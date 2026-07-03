Audit Report

## Title
`getAvailableAssetAmount` Does Not Subtract `queuedWithdrawalsBuffer`, Allowing Queued Withdrawals to Deplete Instant-Withdrawal Liquidity — (File: `contracts/LRTWithdrawalManager.sol`)

## Summary

`getAvailableAssetAmount` computes available assets using the full `LRTUnstakingVault` balance via `getTotalAssetDeposits`, without subtracting the `queuedWithdrawalsBuffer` reserved to protect instant-withdrawal liquidity. An unprivileged user can queue a withdrawal that consumes the entire vault balance including the buffer. When the operator subsequently calls `unlockQueue` (a routine operational step), the vault is drained, and `getAssetsAvailableForInstantWithdrawal` returns zero, temporarily freezing all instant withdrawals for all users.

## Finding Description

Two separate liquidity reservations exist but are never reconciled:

**1. `queuedWithdrawalsBuffer` in `LRTUnstakingVault`**

`getAssetsAvailableForInstantWithdrawal` subtracts the buffer from the vault balance to protect instant-withdrawal liquidity: [1](#0-0) 

**2. `getAvailableAssetAmount` in `LRTWithdrawalManager` ignores the buffer**

This function gates new queued withdrawal requests. It calls `getTotalAssetDeposits`, which includes the full raw vault balance (`IERC20(asset).balanceOf(lrtUnstakingVault)`), and only subtracts `assetsCommitted[asset]` — never `queuedWithdrawalsBuffer`: [2](#0-1) [3](#0-2) 

**3. `_createUnlockParams` also uses the full vault balance**

When `unlockQueue` is called, `_createUnlockParams` sets `totalAvailableAssets` to `unstakingVault.balanceOf(asset)` — the full balance, not the buffer-adjusted amount: [4](#0-3) 

**4. `unlockQueue` drains the vault**

`_unlockWithdrawalRequests` allocates up to `totalAvailableAssets` (full balance) to queued requests, then `unstakingVault.redeem` transfers that amount out: [5](#0-4) 

**5. `instantWithdrawal` then reverts for all users**

After the vault is drained to zero, `getAssetsAvailableForInstantWithdrawal` returns `max(0, 0 - buffer) = 0`, causing every instant withdrawal to revert: [6](#0-5) 

The existing check at `initiateWithdrawal` (`expectedAssetAmount > getAvailableAssetAmount`) is insufficient because `getAvailableAssetAmount` itself does not account for the buffer: [7](#0-6) 

## Impact Explanation

**Medium — Temporary freezing of funds.** After the vault is drained, `instantWithdrawal` reverts with `CantInstantWithdrawMoreThanAvailable` for every user until the operator either deposits more assets into the vault or reduces `queuedWithdrawalsBuffer`. Funds are not permanently lost but are inaccessible for instant withdrawal during this window. This matches the "Medium — Temporary freezing of funds" impact tier.

## Likelihood Explanation

- `queuedWithdrawalsBuffer` is set during normal protocol operation; it is not an edge-case configuration.
- Any unprivileged user can call `initiateWithdrawal` with an `rsETHAmount` whose `expectedAssetAmount` equals or exceeds the buffer, since `getAvailableAssetAmount` does not subtract it.
- The operator's subsequent `unlockQueue` call is a routine operational step; the operator acts in good faith and has no on-chain signal that the queued withdrawal will consume the buffer.
- No special permissions, front-running, or oracle manipulation are required from the attacker.

## Recommendation

In `getAvailableAssetAmount`, subtract `queuedWithdrawalsBuffer` from the vault's contribution to `totalAssets` before computing availability:

```solidity
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    uint256 buffer = unstakingVault.queuedWithdrawalsBuffer(asset);
    uint256 adjustedTotal = totalAssets > buffer ? totalAssets - buffer : 0;
    availableAssetAmount = adjustedTotal > assetsCommitted[asset] ? adjustedTotal - assetsCommitted[asset] : 0;
}
```

Alternatively, expose a `getAssetsAvailableForQueuedWithdrawal` view on `LRTUnstakingVault` that mirrors the buffer-adjusted logic, and use it as the cap in `initiateWithdrawal`.

## Proof of Concept

**Setup:**
- `LRTUnstakingVault` holds 100 ETH; all other protocol locations hold 0.
- Operator sets `queuedWithdrawalsBuffer[ETH] = 80 ETH`.
- `assetsCommitted[ETH] = 0`.

**Step 1 — Attacker queues a withdrawal:**
- `getAvailableAssetAmount(ETH)` = `getTotalAssetDeposits(ETH)` − 0 = 100 ETH (buffer not subtracted).
- Attacker calls `initiateWithdrawal(ETH, rsETHAmount)` where `expectedAssetAmount = 100 ETH`.
- Check passes: 100 ETH ≤ 100 ETH available.
- `assetsCommitted[ETH]` becomes 100 ETH. [8](#0-7) 

**Step 2 — Operator calls `unlockQueue` (routine operation):**
- `_createUnlockParams` sets `totalAvailableAssets = unstakingVault.balanceOf(ETH) = 100 ETH`.
- `_unlockWithdrawalRequests` allocates 100 ETH to the request.
- `unstakingVault.redeem(ETH, 100 ETH)` drains the vault to 0. [9](#0-8) 

**Step 3 — Instant withdrawals are frozen:**
- `getAssetsAvailableForInstantWithdrawal(ETH)` = max(0, 0 − 80) = **0**.
- Every `instantWithdrawal` call reverts with `CantInstantWithdrawMoreThanAvailable`.
- Funds remain frozen until the operator deposits more ETH or lowers the buffer. [1](#0-0)

### Citations

**File:** contracts/LRTUnstakingVault.sol (L235-237)
```text
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
```

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L231-233)
```text
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }
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

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTDepositPool.sol (L458-461)
```text
        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```
