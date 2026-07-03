### Title
`LRTUnstakingVault.transferAssetToNodeDelegator` / `transferETHToNodeDelegator` Don't Check That Remaining Balance Covers `queuedWithdrawalsBuffer` - (File: contracts/LRTUnstakingVault.sol)

---

### Summary

`LRTUnstakingVault` maintains a `queuedWithdrawalsBuffer[asset]` that is supposed to reserve a portion of the vault's balance exclusively for servicing queued (non-instant) withdrawals. However, `transferAssetToNodeDelegator` and `transferETHToNodeDelegator` transfer assets out of the vault to node delegators without verifying that the remaining balance still covers `queuedWithdrawalsBuffer[asset]`. This is the direct analog of the Derby `rebalanceXChain` bug: funds are sent out without checking that the reserved amount remains intact.

---

### Finding Description

`LRTUnstakingVault` stores a per-asset buffer:

```solidity
// Portion of the vault reserved for servicing queued withdrawals; unavailable for instant withdrawals.
mapping(address asset => uint256 buffer) public queuedWithdrawalsBuffer;
``` [1](#0-0) 

`getAssetsAvailableForInstantWithdrawal` correctly subtracts this buffer before allowing instant withdrawals:

```solidity
availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
``` [2](#0-1) 

However, `transferAssetToNodeDelegator` and `transferETHToNodeDelegator` perform no such check:

```solidity
function transferAssetToNodeDelegator(uint256 ndcIndex, address asset, uint256 amount)
    external nonReentrant onlyAssetTransferRole onlySupportedAsset(asset)
{
    ...
    IERC20(asset).safeTransfer(nodeDelegator, amount);  // no buffer check
}
``` [3](#0-2) 

```solidity
function transferETHToNodeDelegator(uint256 ndcIndex, uint256 amount)
    external nonReentrant onlyAssetTransferRole onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    ...
    INodeDelegator(nodeDelegator).sendETHFromUnstakingVaultToNDC{ value: amount }();  // no buffer check
}
``` [4](#0-3) 

The `unlockQueue` function in `LRTWithdrawalManager` uses `unstakingVault.balanceOf(asset)` as the total available assets for unlocking queued withdrawals:

```solidity
totalAvailableAssets: unstakingVault.balanceOf(asset)
``` [5](#0-4) 

If the vault balance is drained below `queuedWithdrawalsBuffer[asset]` by a `transferAssetToNodeDelegator` / `transferETHToNodeDelegator` call, `unlockQueue` will see insufficient `totalAvailableAssets` and be unable to unlock queued withdrawal requests, freezing those users' funds.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Queued withdrawal users who have already submitted `initiateWithdrawal` (burning or locking their rsETH) and are waiting for `unlockQueue` to service their requests will find that the vault has insufficient balance to unlock their withdrawals. The `queuedWithdrawalsBuffer` protection — which exists precisely to guarantee these users are served — is silently bypassed. Funds remain frozen until the vault is replenished (e.g., by completing an EigenLayer withdrawal cycle back into the vault).

---

### Likelihood Explanation

**Medium.** The Asset Transfer Role is a legitimate operational role expected to move funds between the unstaking vault and node delegators for re-staking as part of normal protocol operations. There is no on-chain enforcement preventing the role from transferring an amount that exceeds `vaultBalance - queuedWithdrawalsBuffer[asset]`. A well-intentioned operator who does not manually track the buffer could inadvertently drain it. This is not a malicious-operator scenario; it is a missing protocol-level invariant.

---

### Recommendation

Add a post-transfer check in both `transferAssetToNodeDelegator` and `transferETHToNodeDelegator` to enforce that the remaining vault balance is at least `queuedWithdrawalsBuffer[asset]`:

```solidity
uint256 remaining = balanceOf(asset);
if (remaining < queuedWithdrawalsBuffer[asset]) {
    revert InsufficientBalanceAfterTransfer();
}
```

Alternatively, expose a `getTransferableAmount(asset)` view that returns `max(0, balanceOf(asset) - queuedWithdrawalsBuffer[asset])` and enforce it as the upper bound on transfers.

---

### Proof of Concept

1. Operator calls `setQueuedWithdrawalsBuffer(ETH, 200 ETH)` to protect queued ETH withdrawals.
2. `LRTUnstakingVault` holds 1000 ETH (received from completed EigenLayer withdrawals).
3. Users call `initiateWithdrawal(ETH, ...)` — `assetsCommitted[ETH]` grows; they expect the 200 ETH buffer to guarantee their eventual payout.
4. Asset Transfer Role calls `transferETHToNodeDelegator(ndcIndex, 900)` — 900 ETH is sent to a node delegator for re-staking. No buffer check occurs.
5. Vault now holds 100 ETH; `queuedWithdrawalsBuffer[ETH]` is still 200 ETH.
6. Operator calls `unlockQueue(ETH, ...)`. `_createUnlockParams` reads `unstakingVault.balanceOf(ETH)` = 100 ETH as `totalAvailableAssets`.
7. `_unlockWithdrawalRequests` exits immediately at `if (availableAssetAmount < payoutAmount) break` for any request requiring more than 100 ETH.
8. Queued withdrawal users cannot complete their withdrawals — funds are temporarily frozen until the vault is replenished. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTUnstakingVault.sol (L42-43)
```text
    // Portion of the vault reserved for servicing queued withdrawals; unavailable for instant withdrawals.
    mapping(address asset => uint256 buffer) public queuedWithdrawalsBuffer;
```

**File:** contracts/LRTUnstakingVault.sol (L112-146)
```text
    function transferAssetToNodeDelegator(
        uint256 ndcIndex,
        address asset,
        uint256 amount
    )
        external
        nonReentrant
        onlyAssetTransferRole
        onlySupportedAsset(asset)
    {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.depositPool());
        address[] memory nodeDelegatorQueue = lrtDepositPool.getNodeDelegatorQueue();
        address nodeDelegator = nodeDelegatorQueue[ndcIndex];
        IERC20(asset).safeTransfer(nodeDelegator, amount);
    }

    /// @notice transfers ETH lying in this LRTUnstakingVault to node delegator contract
    /// @dev only callable by Asset Transfer Role
    /// @param ndcIndex Index of NodeDelegator contract address in nodeDelegatorQueue
    /// @param amount ETH amount to transfer
    function transferETHToNodeDelegator(
        uint256 ndcIndex,
        uint256 amount
    )
        external
        nonReentrant
        onlyAssetTransferRole
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.depositPool());
        address[] memory nodeDelegatorQueue = lrtDepositPool.getNodeDelegatorQueue();
        address nodeDelegator = nodeDelegatorQueue[ndcIndex];
        INodeDelegator(nodeDelegator).sendETHFromUnstakingVaultToNDC{ value: amount }();
        emit EthTransferred(nodeDelegator, amount);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L235-237)
```text
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
```

**File:** contracts/LRTWithdrawalManager.sol (L797-800)
```text
            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

**File:** contracts/LRTWithdrawalManager.sol (L837-850)
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
```
