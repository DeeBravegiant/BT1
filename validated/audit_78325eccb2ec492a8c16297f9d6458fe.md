### Title
Changing `LRT_WITHDRAW_MANAGER` in `LRTConfig` While Pending Withdrawals Exist Permanently Freezes User Funds - (File: contracts/LRTUnstakingVault.sol)

---

### Summary

`LRTUnstakingVault.redeem()` authenticates its caller by dynamically reading `lrtConfig.withdrawManager()` at call time. If the admin updates `LRT_WITHDRAW_MANAGER` in `LRTConfig` while users have pending withdrawal requests in the old `LRTWithdrawalManager`, the old withdrawal manager loses the ability to call `redeem()`, permanently freezing the rsETH held in the old contract and blocking all pending user withdrawals.

---

### Finding Description

When a user initiates a withdrawal via `LRTWithdrawalManager`, their rsETH is transferred to the withdrawal manager and a `WithdrawalRequest` struct is stored:

```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number
});
``` [1](#0-0) 

The struct records no reference to the unstaking vault or the withdrawal manager's own address at creation time.

When `unlockQueue()` is later called to service those requests, it fetches the unstaking vault address dynamically from `lrtConfig` and calls `redeem()`:

```solidity
ILRTUnstakingVault unstakingVault =
    ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
...
unstakingVault.redeem(asset, assetAmountUnlocked);
``` [2](#0-1) 

`LRTUnstakingVault.redeem()` is guarded by `onlyLRTWithdrawalManager`, which dynamically resolves the authorized caller from `lrtConfig` at execution time:

```solidity
modifier onlyLRTWithdrawalManager() {
    if (msg.sender != lrtConfig.withdrawManager()) {
        revert CallerNotLRTWithdrawalManager();
    }
    _;
}
``` [3](#0-2) 

The admin can change `LRT_WITHDRAW_MANAGER` at any time via `LRTConfig.setContract()`:

```solidity
function setContract(bytes32 contractKey, address contractAddress)
    external onlyRole(DEFAULT_ADMIN_ROLE) {
    _setContract(contractKey, contractAddress);
}
``` [4](#0-3) 

Once `LRT_WITHDRAW_MANAGER` is updated to a new address, `lrtConfig.withdrawManager()` returns the new address. Any subsequent call to `unstakingVault.redeem()` from the **old** withdrawal manager reverts with `CallerNotLRTWithdrawalManager`. The rsETH already transferred into the old withdrawal manager is stuck, and `unlockQueue()` can never complete for any pending request in that contract.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds.**

Users who initiated withdrawals before the contract address change have their rsETH locked in the old `LRTWithdrawalManager`. The old contract can no longer call `unstakingVault.redeem()`, so `unlockQueue()` always reverts. Users cannot complete withdrawals or recover their rsETH until the admin either reverts the change or performs a manual recovery. Without admin intervention the freeze is permanent; with it, it is temporary.

---

### Likelihood Explanation

**Likelihood: Medium.**

Upgrading the withdrawal manager to a new implementation is a realistic and expected protocol maintenance operation (e.g., adding new features, fixing bugs). There is no on-chain mechanism that forces the admin to drain or migrate pending requests before changing the address. The window between a user initiating a withdrawal and the queue being unlocked spans multiple blocks (enforced by `withdrawalDelayBlocks`), making it likely that pending requests exist at the time of any upgrade.

---

### Recommendation

Record the unstaking vault address (and/or the withdrawal manager's own address) inside the `WithdrawalRequest` struct at creation time, analogous to the recommendation in the reference report. Alternatively, require that `nextLockedNonce[asset] == nextUnusedNonce[asset]` (i.e., no pending requests exist) before allowing `LRT_WITHDRAW_MANAGER` or `LRT_UNSTAKING_VAULT` to be changed in `LRTConfig`.

---

### Proof of Concept

1. User calls `initiateWithdrawal(ETH, rsETHAmount)` on the current `LRTWithdrawalManager` (address `WM_old`). rsETH is transferred to `WM_old`; a `WithdrawalRequest` is stored.
2. Admin calls `LRTConfig.setContract(LRT_WITHDRAW_MANAGER, WM_new)`.
3. `lrtConfig.withdrawManager()` now returns `WM_new`.
4. Operator calls `WM_old.unlockQueue(ETH, ...)`. Inside, `WM_old` calls `LRTUnstakingVault.redeem(ETH, amount)`.
5. `onlyLRTWithdrawalManager` checks `msg.sender (WM_old) != lrtConfig.withdrawManager() (WM_new)` → reverts with `CallerNotLRTWithdrawalManager`.
6. The user's rsETH remains locked in `WM_old` indefinitely; the withdrawal can never be completed.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L284-307)
```text
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L751-753)
```text
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTUnstakingVault.sol (L54-59)
```text
    modifier onlyLRTWithdrawalManager() {
        if (msg.sender != lrtConfig.withdrawManager()) {
            revert CallerNotLRTWithdrawalManager();
        }
        _;
    }
```

**File:** contracts/LRTConfig.sol (L237-239)
```text
    function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setContract(contractKey, contractAddress);
    }
```
