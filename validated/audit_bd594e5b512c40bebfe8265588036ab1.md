### Title
`instantWithdrawal` Drains `LRTUnstakingVault` Below `assetsCommitted`, Temporarily Freezing Queued Withdrawal Users - (File: contracts/LRTWithdrawalManager.sol / contracts/LRTUnstakingVault.sol)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal()` checks available vault liquidity against a static, manually-set `queuedWithdrawalsBuffer` rather than the dynamically-tracked `assetsCommitted` mapping. When instant withdrawals are enabled and the buffer is zero (the default) or set too low, any rsETH holder can drain `LRTUnstakingVault` below the amount already committed to pending queued withdrawal requests, causing `unlockQueue()` to stall and temporarily freezing queued-withdrawal users' funds.

---

### Finding Description

`LRTWithdrawalManager.initiateWithdrawal()` records the ETH/LST amount owed to a queued-withdrawal user in `assetsCommitted[asset]`: [1](#0-0) 

`assetsCommitted` is the protocol's authoritative record of how much vault liquidity is already spoken for. It is decremented only when `unlockQueue()` actually pulls those funds out of `LRTUnstakingVault`.

`unlockQueue()` determines how many requests it can service by reading the vault's raw balance: [2](#0-1) 

If the vault balance is less than the next request's payout, processing stops: [3](#0-2) 

`instantWithdrawal()` pulls directly from the vault, gated only by `getAssetsAvailableForInstantWithdrawal()`: [4](#0-3) 

`getAssetsAvailableForInstantWithdrawal()` subtracts a static, manually-set `queuedWithdrawalsBuffer` — **not** `assetsCommitted`: [5](#0-4) 

`queuedWithdrawalsBuffer` defaults to `0` (Solidity mapping default) and is set by operators via `setQueuedWithdrawalsBuffer()`: [6](#0-5) 

There is no on-chain enforcement that `queuedWithdrawalsBuffer[asset] >= assetsCommitted[asset]`. The two values are completely independent. Instant withdrawals therefore consume vault liquidity that is already promised to queued-withdrawal users, without any accounting correction to `assetsCommitted`.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

When instant withdrawals drain the vault below `assetsCommitted`, `unlockQueue()` cannot advance the queue. Queued-withdrawal users' rsETH has already been transferred to the withdrawal manager at `initiateWithdrawal()` time; they cannot reclaim it and cannot receive their asset until operators manually replenish the vault (e.g., by completing a new EigenLayer withdrawal cycle, which takes ≥7 days). Their funds are not permanently lost but are frozen for an indeterminate period beyond the promised withdrawal delay.

---

### Likelihood Explanation

**Low.** Two conditions must hold simultaneously:

1. The manager has called `setInstantWithdrawalEnabled(asset, true)` — disabled by default.
2. `queuedWithdrawalsBuffer[asset]` is zero (the default) or set to a value below the current `assetsCommitted[asset]`.

Both conditions are realistic in normal operation: instant withdrawal is a documented feature, and the buffer is a static value that operators must manually keep in sync with a continuously changing `assetsCommitted`. A period where the buffer lags behind `assetsCommitted` is expected during normal protocol activity.

---

### Recommendation

Replace the static `queuedWithdrawalsBuffer` with a dynamic read of `assetsCommitted` from `LRTWithdrawalManager` inside `getAssetsAvailableForInstantWithdrawal()`:

```solidity
// LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal
function getAssetsAvailableForInstantWithdrawal(address asset) external view returns (uint256) {
    uint256 vaultBalance = balanceOf(asset);
    // Read the live committed amount from the withdrawal manager
    uint256 committed = ILRTWithdrawalManager(withdrawalManager).assetsCommitted(asset);
    return committed >= vaultBalance ? 0 : vaultBalance - committed;
}
```

This mirrors the fix recommended in the reference report: the contract that holds liquidity must account for the amount already locked by pending claims before allowing further withdrawals.

---

### Proof of Concept

**Setup**: Instant withdrawal is enabled for ETH; `queuedWithdrawalsBuffer[ETH]` = 0 (default). The `LRTUnstakingVault` holds 100 ETH (moved there by operators from EigenLayer).

1. **Alice** calls `initiateWithdrawal(ETH, rsETHAmount)` where `getExpectedAssetAmount` returns 80 ETH.
   - `assetsCommitted[ETH]` → 80 ETH.
   - Alice's rsETH is transferred to `LRTWithdrawalManager`.

2. **Bob** (any rsETH holder) calls `instantWithdrawal(ETH, rsETHAmount2)` where `getExpectedAssetAmount` returns 80 ETH.
   - `getAssetsAvailableForInstantWithdrawal(ETH)` = `100 - 0` = 100 ETH → check passes.
   - `LRTUnstakingVault` balance drops to 20 ETH.

3. Operator calls `unlockQueue(ETH, ...)`.
   - `params.totalAvailableAssets` = `unstakingVault.balanceOf(ETH)` = 20 ETH.
   - Alice's request requires 80 ETH → `availableAssetAmount (20) < payoutAmount (80)` → **loop breaks immediately**.
   - Alice's withdrawal remains locked.

4. Alice cannot call `completeWithdrawal` (request is still in locked state). Her 80 ETH equivalent is frozen until operators complete a new EigenLayer withdrawal cycle (≥7 days) and replenish the vault. [7](#0-6) [8](#0-7) [5](#0-4) [9](#0-8)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
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
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L796-800)
```text

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

**File:** contracts/LRTWithdrawalManager.sol (L846-851)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```

**File:** contracts/LRTUnstakingVault.sol (L199-208)
```text
    function setQueuedWithdrawalsBuffer(
        address asset,
        uint256 buffer
    )
        external
        onlyLRTOperator
        onlySupportedAsset(asset)
    {
        queuedWithdrawalsBuffer[asset] = buffer;
        emit QueuedWithdrawalsBufferUpdated(asset, buffer);
```

**File:** contracts/LRTUnstakingVault.sol (L229-238)
```text
    function getAssetsAvailableForInstantWithdrawal(address asset)
        external
        view
        onlySupportedAsset(asset)
        returns (uint256 availableAmount)
    {
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
```
