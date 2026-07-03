### Title
Permanent ETH Freeze in `LRTWithdrawalManager` When Withdrawing User Is a Contract Without `receive()` — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

The `LRTWithdrawalManager` withdrawal lifecycle is split across two separate transactions: `unlockQueue` (which burns rsETH and moves ETH into the manager) and `completeWithdrawal` (which delivers ETH to the user). If the withdrawing user is a smart contract without a `receive()` function, the ETH delivery always reverts, but the rsETH has already been permanently burned in the prior `unlockQueue` call. No recovery path exists in the current code, causing a permanent freeze of the user's ETH.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` proceeds in two distinct on-chain transactions:

**Step 1 — `unlockQueue` (operator-triggered):**

`unlockQueue` burns rsETH held by the manager and redeems the corresponding ETH from the unstaking vault into the manager's own balance. [1](#0-0) 

After this call, the rsETH is **permanently burned** and the ETH sits in `LRTWithdrawalManager`.

**Step 2 — `completeWithdrawal` / `completeWithdrawalForUser` (user or operator-triggered):**

`_processWithdrawalCompletion` deletes the withdrawal request from storage and then calls `_transferAsset` to push ETH to the user. [2](#0-1) 

`_transferAsset` for ETH uses a low-level `call`: [3](#0-2) 

If the user is a smart contract without a `receive()` function, `call{value: amount}("")` returns `false`, triggering `revert EthTransferFailed()`. Because this revert unwinds the entire transaction, the `delete withdrawalRequests[requestId]` and `popFront()` are also rolled back — the withdrawal request remains in the unlocked queue indefinitely.

**No recovery path exists:**

- `sweepRemainingAssets` is gated by `hasUnlockedWithdrawals(asset)`, which returns `true` as long as `unlockedWithdrawalsCount[asset] > 0`. Since the revert prevents the decrement at line 717, sweeping is permanently blocked. [4](#0-3) 

- `completeWithdrawalForUser` (operator path) calls the same `_processWithdrawalCompletion` and hits the same revert. [5](#0-4) 

- There is no admin function to redirect a withdrawal to a different recipient or to cancel an already-unlocked request.

The result: rsETH is burned, ETH is locked in `LRTWithdrawalManager`, and neither the user nor the protocol can recover it without a contract upgrade.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

The user's rsETH is irreversibly burned during `unlockQueue`. The corresponding ETH is trapped in `LRTWithdrawalManager` with no on-chain mechanism to release it. The user loses their entire withdrawal amount.

---

### Likelihood Explanation

**Low.** The affected user must be a smart contract (not an EOA) that:
1. Holds rsETH and has approved `LRTWithdrawalManager` to spend it.
2. Calls `initiateWithdrawal` with `asset == ETH_TOKEN`.
3. Does not implement a `receive()` or `fallback()` function.

This is realistic for protocol integrations, vaults, or smart contract wallets that hold rsETH but were not designed to receive raw ETH. The developer comment on `completeWithdrawalForUser` — *"Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH"* — shows awareness of ETH-delivery edge cases but incorrectly dismisses the permanent-freeze scenario. [6](#0-5) 

---

### Recommendation

Validate that the withdrawing address can receive ETH **before** accepting the withdrawal request in `initiateWithdrawal`. One approach is to attempt a zero-value ETH transfer to `msg.sender` at initiation time and revert if it fails. Alternatively, restrict ETH withdrawals to EOAs only (check `msg.sender.code.length == 0`), or provide an admin escape hatch that can redirect an unlocked withdrawal to an alternate recipient when the primary recipient is unable to receive ETH.

---

### Proof of Concept

```solidity
// A contract that holds rsETH but has no receive() function
contract NoReceiveWallet {
    function initiateETHWithdrawal(
        address withdrawalManager,
        address rsETH,
        uint256 rsETHAmount
    ) external {
        IERC20(rsETH).approve(withdrawalManager, rsETHAmount);
        // rsETH transferred to LRTWithdrawalManager; withdrawal request created
        ILRTWithdrawalManager(withdrawalManager)
            .initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
    }
}
```

1. `NoReceiveWallet` calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`.
   - rsETH is transferred from `NoReceiveWallet` to `LRTWithdrawalManager`. [7](#0-6) 
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`.
   - rsETH is **burned** from `LRTWithdrawalManager`. [8](#0-7) 
   - ETH is moved from `LRTUnstakingVault` into `LRTWithdrawalManager`. [9](#0-8) 
3. `NoReceiveWallet` calls `completeWithdrawal(ETH_TOKEN, "")`.
   - `_transferAsset` attempts `payable(NoReceiveWallet).call{value: amount}("")`.
   - Call returns `false` → `revert EthTransferFailed()`.
   - Entire transaction reverts; withdrawal request remains in queue.
4. Operator calls `completeWithdrawalForUser(ETH_TOKEN, address(NoReceiveWallet), "")` → same revert.
5. `sweepRemainingAssets(ETH_TOKEN)` → reverts because `hasUnlockedWithdrawals(ETH_TOKEN) == true`.
6. **ETH is permanently locked in `LRTWithdrawalManager`. rsETH is permanently burned.**

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
```

**File:** contracts/LRTWithdrawalManager.sol (L192-203)
```text
    function completeWithdrawalForUser(
        address asset,
        address user,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        _processWithdrawalCompletion(asset, user, referralId);
        emit AssetWithdrawalCompletedBy(msg.sender);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
```

**File:** contracts/LRTWithdrawalManager.sol (L712-734)
```text
        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;

        // If Aave integration is enabled and asset is ETH, withdraw from Aave if needed
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
            uint256 contractBalance = address(this).balance;
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L876-883)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
    }
```
