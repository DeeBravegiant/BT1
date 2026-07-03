### Title
ETH Withdrawal Permanently Frozen When Recipient Cannot Receive ETH - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary
The withdrawal lifecycle in `LRTWithdrawalManager` is split across two separate transactions. The operator burns rsETH in `unlockQueue`, and the user later calls `completeWithdrawal` to receive ETH. If the user's address is a smart contract that cannot receive ETH, `completeWithdrawal` permanently reverts with no recovery path, while the user's rsETH is already irreversibly burned.

---

### Finding Description

The withdrawal flow is:

1. **`initiateWithdrawal`** — user's rsETH is transferred into the contract and a `WithdrawalRequest` is recorded.
2. **`unlockQueue`** (operator) — rsETH is burned and the request is marked unlocked: [1](#0-0) 
3. **`completeWithdrawal`** / **`completeWithdrawalForUser`** — calls `_processWithdrawalCompletion`, which ends with: [2](#0-1) 

The internal `_transferAsset` for ETH is:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
``` [3](#0-2) 

If `to` is a smart contract that rejects ETH (no `receive()`/`fallback()`, or one that conditionally reverts — e.g., a paused vault, an upgraded multisig, or a DAO contract), the call returns `false` and the entire `completeWithdrawal` transaction reverts. Because rsETH was burned in a prior, already-finalized `unlockQueue` transaction, the burn is **not** rolled back. The user's rsETH is gone permanently, but the ETH remains locked in `LRTWithdrawalManager` with no recovery path.

`completeWithdrawalForUser` (operator-initiated) calls the same `_processWithdrawalCompletion` path and hits the same revert: [4](#0-3) 

The `sweepRemainingAssets` function cannot help because it requires `unlockedWithdrawalsCount[asset] == 0`, which is never decremented when `completeWithdrawal` reverts: [5](#0-4) 

There is no admin escape hatch to redirect a stuck ETH withdrawal to an alternate address.

---

### Impact Explanation

**Critical — Permanent freezing of funds.** The user's rsETH is burned in `unlockQueue` (an operator transaction that cannot be undone). If `completeWithdrawal` always reverts for that user, the corresponding ETH is locked in `LRTWithdrawalManager` indefinitely. The user loses both their rsETH and their ETH with no on-chain recovery mechanism.

---

### Likelihood Explanation

**Low-to-medium.** Smart contract wallets (Gnosis Safe, DAO treasuries, yield vaults) are common DeFi participants. A contract without a `receive()` function, or whose `receive()` reverts under certain conditions (e.g., after a contract upgrade, when the contract is paused, or due to a gas-limited callback), would trigger this. The user may not anticipate the issue at withdrawal initiation time, especially if the contract's ETH-receiving capability changes between `initiateWithdrawal` and `completeWithdrawal` (which can be separated by 8+ days due to `withdrawalDelayBlocks`): [6](#0-5) 

---

### Recommendation

Replace the push-payment pattern with a pull-payment pattern for ETH withdrawals: instead of calling `payable(to).call{value: amount}("")` inside `completeWithdrawal`, record the owed ETH in a `pendingETH[user]` mapping and provide a separate `claimETH()` function the user can call from any address they control. This mirrors the `claims[token]` pattern recommended in the external report.

---

### Proof of Concept

1. A DAO treasury contract (no `receive()`) calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is escrowed in `LRTWithdrawalManager`.
2. After the delay, the operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned via `burnFrom`. The withdrawal is now unlocked.
3. The DAO calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` executes `payable(daoAddress).call{value: amount}("")`. The DAO has no `receive()`, so `sent == false`. `EthTransferFailed` is reverted.
4. All state changes in step 3 revert (nonce pop, request deletion, count decrement). The withdrawal request remains unlocked but uncollectable.
5. The operator calls `completeWithdrawalForUser(ETH_TOKEN, daoAddress, "")` — same revert.
6. The DAO's rsETH is permanently burned. The ETH is permanently locked in `LRTWithdrawalManager`. No admin function can redirect it to the DAO.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
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

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
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
