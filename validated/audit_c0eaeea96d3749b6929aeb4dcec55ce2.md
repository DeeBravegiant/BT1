Audit Report

## Title
ETH Permanently Frozen for Non-Payable Smart Contract Withdrawers — (`contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTWithdrawalManager._transferAsset` unconditionally pushes ETH to the recipient via a low-level `call`. If the recipient is a smart contract without a `receive()` or `fallback()` function, the call returns `false` and the transaction reverts with `EthTransferFailed`. Because rsETH is burned during `unlockQueue` (before `completeWithdrawal` is ever called), the user's rsETH is permanently gone while the ETH remains locked in the contract with no admin rescue path.

## Finding Description
The withdrawal lifecycle has a critical ordering issue:

**Step 1 — `initiateWithdrawal`** (L166): rsETH is pulled from the user into `LRTWithdrawalManager`. [1](#0-0) 

**Step 2 — `unlockQueue`** (L305): rsETH is burned from the contract's balance. This is irreversible and happens before any `completeWithdrawal` call. [2](#0-1) 

**Step 3 — `completeWithdrawal` / `completeWithdrawalForUser`** calls `_processWithdrawalCompletion`, which:
- Pops the nonce from the user's queue (L705)
- Deletes the `withdrawalRequests` entry (L712)
- Decrements `unlockedWithdrawalsCount[asset]` (L717)
- Then calls `_transferAsset(asset, user, ...)` (L734) [3](#0-2) 

**`_transferAsset`** (L876-883) pushes ETH via:
```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
``` [4](#0-3) 

If `to` is a contract without `receive()`, `sent == false`, and `EthTransferFailed` is thrown. The EVM reverts the entire transaction, restoring `unlockedWithdrawalsCount[asset]` to its pre-call value. Every subsequent call to `completeWithdrawal` or `completeWithdrawalForUser` for this user will fail identically.

The only potential admin escape hatch, `sweepRemainingAssets`, is permanently blocked:
```solidity
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
``` [5](#0-4) 

Because `unlockedWithdrawalsCount[ETH]` is always restored by the revert, `hasUnlockedWithdrawals(ETH)` remains `true` indefinitely. No other function in the contract can redirect or rescue the frozen ETH. The rsETH burned in Step 2 is unrecoverable.

## Impact Explanation
**Critical — Permanent freezing of funds.** A smart contract depositor (e.g., a Gnosis Safe multisig, DAO treasury, or vault contract) that holds rsETH, initiates an ETH withdrawal, and lacks a `receive()` function will have its ETH permanently locked in `LRTWithdrawalManager`. The rsETH is already burned; neither the user, the operator, nor any admin can recover the ETH. This directly matches the Critical impact tier: permanent freezing of user funds.

## Likelihood Explanation
Low-to-medium. The scenario requires a smart contract without a `receive()` function to hold rsETH and initiate an ETH withdrawal. Many protocol-level contracts — immutable vault contracts, proxy contracts without ETH fallback, certain multisig configurations — satisfy this condition by design. The protocol's institutional user base makes this non-negligible. No attacker is required; the victim triggers the freeze themselves through normal protocol usage.

## Recommendation
Apply the pull-over-push pattern for ETH withdrawals: instead of pushing ETH to the user in `_processWithdrawalCompletion`, record the claimable amount in a `mapping(address => uint256) claimableETH` and let the user (or any caller on their behalf) pull it via a separate `claimETH(address user)` function. Alternatively, add an admin-only `rescueStuckWithdrawal(address asset, address user, address recipient)` function that can redirect a permanently stuck withdrawal to a different address, bypassing the frozen recipient.

## Proof of Concept
1. `VaultContract` (no `receive()`) holds rsETH and calls `initiateWithdrawal(ETH_TOKEN, amount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned at L305; `unlockedWithdrawalsCount[ETH]` becomes 1.
3. `VaultContract` calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` executes `payable(VaultContract).call{value: amount}("")`. The call returns `false` (no `receive()`). `EthTransferFailed` is thrown; the entire transaction reverts. `unlockedWithdrawalsCount[ETH]` is restored to 1.
4. Operator calls `completeWithdrawalForUser(ETH_TOKEN, VaultContract, "")`. Same revert.
5. Operator attempts `sweepRemainingAssets(ETH_TOKEN)`. Reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH] == 1`.
6. ETH is permanently locked in `LRTWithdrawalManager`. `VaultContract`'s rsETH is gone.

**Foundry test plan:** Deploy a `NoReceive` contract (no `receive()`/`fallback()`), fund it with rsETH, call `initiateWithdrawal`, simulate `unlockQueue` (burn rsETH, increment `unlockedWithdrawalsCount`), then assert that `completeWithdrawal` reverts with `EthTransferFailed` and that `sweepRemainingAssets` reverts with `PendingWithdrawalsExist`, while `address(lrtWithdrawalManager).balance > 0`.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

**File:** contracts/LRTWithdrawalManager.sol (L402-403)
```text
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

**File:** contracts/LRTWithdrawalManager.sol (L705-717)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;
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
