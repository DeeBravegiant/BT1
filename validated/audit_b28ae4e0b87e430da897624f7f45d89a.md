### Title
EigenLayer Withdrawal Queue Delay Can Temporarily Freeze User Funds When `LRTUnstakingVault` Is Depleted by Instant Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

The `LRTUnstakingVault` acts as the liquidity buffer for servicing both instant and queued user withdrawals. When this buffer is depleted by `instantWithdrawal()` calls (possible when `queuedWithdrawalsBuffer` is zero, the default), replenishing it requires going through EigenLayer's `minWithdrawalDelayBlocks` (~7 days). During this period, `unlockQueue()` reverts, and users who have already initiated withdrawals have their rsETH locked in `LRTWithdrawalManager` for an extended period exceeding one week.

---

### Finding Description

**Step 1 – Instant withdrawals drain the vault.**

`instantWithdrawal()` in `LRTWithdrawalManager` burns the caller's rsETH and calls `unstakingVault.redeem()`, reducing the `LRTUnstakingVault` balance:

```solidity
// LRTWithdrawalManager.sol:231-235
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
```

`getAssetsAvailableForInstantWithdrawal()` returns `vaultBalance - queuedWithdrawalsBuffer[asset]`. Since `queuedWithdrawalsBuffer` defaults to `0`, the entire vault balance is available for instant withdrawals. Multiple users calling `instantWithdrawal()` can drain the vault to zero. [1](#0-0) [2](#0-1) 

**Step 2 – `unlockQueue()` reverts when vault is empty.**

`unlockQueue()` reads the vault balance as `totalAvailableAssets` and immediately reverts if it is zero:

```solidity
// LRTWithdrawalManager.sol:286, 297
UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);
if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

`_createUnlockParams` uses `unstakingVault.balanceOf(asset)` — the raw vault balance — not the instant-withdrawal-adjusted figure:

```solidity
// LRTWithdrawalManager.sol:846-851
return UnlockParams({
    ...
    totalAvailableAssets: unstakingVault.balanceOf(asset)
});
``` [3](#0-2) [4](#0-3) 

**Step 3 – Replenishment requires EigenLayer's 7-day delay.**

To replenish the vault, the operator must call `NodeDelegator.initiateUnstaking()`, which queues a withdrawal from EigenLayer via `IDelegationManager.queueWithdrawals()`. EigenLayer enforces a `minWithdrawalDelayBlocks` (~7 days) before `completeUnstaking()` can be called and assets transferred to the vault:

```solidity
// NodeDelegator.sol:327-329
bytes32[] memory withdrawalRoots = _getDelegationManager().queueWithdrawals(queuedWithdrawalParams);
withdrawalRoot = withdrawalRoots[0];
_getUnstakingVault().increaseUncompletedWithdrawalCount();
``` [5](#0-4) [6](#0-5) 

**Step 4 – Users with queued withdrawals have rsETH frozen.**

Users who called `initiateWithdrawal()` have their rsETH locked in `LRTWithdrawalManager`. Their withdrawal can only be completed after `unlockQueue()` succeeds (which requires vault assets) and `withdrawalDelayBlocks` (initialized to `8 days / 12 seconds`) have elapsed:

```solidity
// LRTWithdrawalManager.sol:94
withdrawalDelayBlocks = 8 days / 12 seconds;
```

```solidity
// LRTWithdrawalManager.sol:715
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

With the vault empty and EigenLayer's 7-day delay, the total lock-up is at minimum **7 days (EigenLayer) + 8 days (protocol delay) = 15 days**, directly mirroring the M-13 scenario. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

Users who have called `initiateWithdrawal()` and had their rsETH locked in `LRTWithdrawalManager` cannot complete their withdrawal while the vault is empty. The operator cannot call `unlockQueue()` (reverts with `AmountMustBeGreaterThanZero`), and replenishment requires at least 7 days via EigenLayer. This constitutes **temporary freezing of funds** for more than one week, meeting the medium-severity threshold.

---

### Likelihood Explanation

- `isInstantWithdrawalEnabled[asset]` must be `true` (set by manager) — a realistic operational state once the feature is live.
- `queuedWithdrawalsBuffer[asset]` defaults to `0`, meaning no protection is in place unless the operator explicitly sets it.
- Any rsETH holder can call `instantWithdrawal()` without special privileges, making the drain path fully user-accessible. [9](#0-8) [10](#0-9) 

---

### Recommendation

1. **Always set `queuedWithdrawalsBuffer` before enabling instant withdrawals.** The buffer should be at least equal to `assetsCommitted[asset]` so that the portion reserved for queued withdrawals cannot be drained by instant withdrawals.
2. **Add a check in `instantWithdrawal()`** that ensures the vault balance after the withdrawal remains sufficient to cover `assetsCommitted[asset]` (the total committed to pending queued withdrawals).
3. **Consider proactively initiating EigenLayer unstaking** well before the vault is expected to be depleted, analogous to the M-13 recommendation of requesting withdrawals two weeks before maturity.

---

### Proof of Concept

```
Initial state:
  LRTUnstakingVault balance = 100 ETH
  queuedWithdrawalsBuffer[ETH] = 0 (default)
  isInstantWithdrawalEnabled[ETH] = true

Step 1: User A calls initiateWithdrawal(ETH, rsETH_A)
  → rsETH_A locked in LRTWithdrawalManager
  → assetsCommitted[ETH] += expectedAmount_A

Step 2: Users B, C, D... call instantWithdrawal(ETH, ...)
  → getAssetsAvailableForInstantWithdrawal = 100 ETH - 0 = 100 ETH
  → vault drained to 0 ETH

Step 3: Operator calls unlockQueue(ETH, ...)
  → _createUnlockParams: totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0
  → REVERT: AmountMustBeGreaterThanZero

Step 4: Operator calls NodeDelegator.initiateUnstaking(...)
  → EigenLayer queues withdrawal, minWithdrawalDelayBlocks ≈ 7 days

Step 5: During 7-day wait:
  → unlockQueue() continues to revert
  → User A's rsETH remains locked in LRTWithdrawalManager
  → User A cannot completeWithdrawal()

Total

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L55-56)
```text
    mapping(address asset => bool) public isInstantWithdrawalEnabled;
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)
```

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
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

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L714-715)
```text
        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L837-851)
```text
    function _createUnlockParams(
        ILRTOracle lrtOracle,
        ILRTUnstakingVault unstakingVault,
        address asset
    )
        internal
        view
        returns (UnlockParams memory)
    {
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```

**File:** contracts/LRTUnstakingVault.sol (L42-43)
```text
    // Portion of the vault reserved for servicing queued withdrawals; unavailable for instant withdrawals.
    mapping(address asset => uint256 buffer) public queuedWithdrawalsBuffer;
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

**File:** contracts/NodeDelegator.sol (L321-330)
```text
        IDelegationManager.QueuedWithdrawalParams[] memory queuedWithdrawalParams =
            new IDelegationManager.QueuedWithdrawalParams[](1);
        queuedWithdrawalParams[0] = IDelegationManagerTypes.QueuedWithdrawalParams({
            strategies: strategies, depositShares: shares, withdrawer: address(this)
        });

        bytes32[] memory withdrawalRoots = _getDelegationManager().queueWithdrawals(queuedWithdrawalParams);
        withdrawalRoot = withdrawalRoots[0];
        _getUnstakingVault().increaseUncompletedWithdrawalCount();
        emit WithdrawalQueued(_getNonce() - 1, address(this), withdrawalRoots);
```

**File:** contracts/external/eigenlayer/interfaces/IDelegationManager.sol (L527-533)
```text
    /**
     * @notice Returns the minimum withdrawal delay in blocks to pass for withdrawals queued to be completable.
     * Also applies to legacy withdrawals so any withdrawals not completed prior to the slashing upgrade will be subject
     * to this longer delay.
     * @dev Backwards-compatible interface to return the `MIN_WITHDRAWAL_DELAY_BLOCKS` value
     */
    function minWithdrawalDelayBlocks() external view returns (uint32);
```
