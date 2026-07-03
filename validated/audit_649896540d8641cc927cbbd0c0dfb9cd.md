### Title
`isWithdrawalClaimable` Does Not Check Whether a Withdrawal Has Already Been Claimed - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.isWithdrawalClaimable` only checks the time condition (`block.timestamp >= unlockTime`) but never checks `withdrawal.claimed`. After a user calls `claimWithdrawal`, the withdrawal record remains in the `withdrawals` mapping with `claimed = true`, and `isWithdrawalClaimable` will continue to return `true` for that already-claimed ID indefinitely, misleading users and off-chain integrators.

### Finding Description
`claimWithdrawal` enforces two conditions before transferring tokens: the unlock time must have passed, and the withdrawal must not have been previously claimed. [1](#0-0) 

After a successful claim, the function sets `withdrawal.claimed = true` and removes the ID from `userWithdrawalIds[msg.sender]`. [2](#0-1) 

However, `isWithdrawalClaimable` only evaluates the time condition and ignores the `claimed` flag entirely: [3](#0-2) 

Because the `withdrawals` mapping is never deleted (only the ID is removed from `userWithdrawalIds`), the stale record persists. Any caller querying `isWithdrawalClaimable` with a previously claimed `_withdrawalId` will receive `true`, while the actual `claimWithdrawal` call will revert with `WithdrawalAlreadyClaimed`.

The contract already exposes a separate `isWithdrawalClaimed` view that correctly reads `withdrawal.claimed`: [4](#0-3) 

The two views are therefore inconsistent: `isWithdrawalClaimable` says "yes, claimable" while `isWithdrawalClaimed` says "yes, already claimed."

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

No funds are at risk because `claimWithdrawal` correctly guards against double-claims. The harm is purely informational: any user, wallet UI, or smart-contract integration that relies on `isWithdrawalClaimable` to decide whether to call `claimWithdrawal` will receive a misleading `true` for already-claimed IDs, causing unexpected reverts and eroding trust in the contract's view layer.

### Likelihood Explanation
Any unprivileged user who has previously claimed a withdrawal and then queries `isWithdrawalClaimable` with the same ID will trigger the inconsistency. This is a normal, expected user action (checking status after claiming), so the likelihood is high that the discrepancy will be encountered in practice.

### Recommendation
Add a check for `withdrawal.claimed` inside `isWithdrawalClaimable`:

```solidity
function isWithdrawalClaimable(uint256 _withdrawalId) external view returns (bool) {
    Withdrawal storage withdrawal = withdrawals[_withdrawalId];
    return !withdrawal.claimed && block.timestamp >= withdrawal.unlockTime;
}
```

This mirrors the two-condition check already present in `claimWithdrawal` and makes the view consistent with the actual claimability state.

### Proof of Concept

1. Alice calls `initiateWithdrawal(100e18)`, receiving `withdrawalId = 1`.
2. Time passes; `block.timestamp >= withdrawal.unlockTime`.
3. Alice calls `claimWithdrawal(1)`. The function sets `withdrawals[1].claimed = true` and removes `1` from `userWithdrawalIds[Alice]`. Alice receives her 100 KERNEL.
4. Alice (or any observer) calls `isWithdrawalClaimable(1)`. The function evaluates only `block.timestamp >= withdrawals[1].unlockTime` — still `true` — and returns `true`.
5. Alice calls `claimWithdrawal(1)` again, expecting to receive tokens. The call reverts with `WithdrawalAlreadyClaimed`.

The view function promised the withdrawal was claimable; the state machine disagreed. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L344-362)
```text
    function claimWithdrawal(uint256 _withdrawalId) external nonReentrant {
        Withdrawal storage withdrawal = withdrawals[_withdrawalId];

        if (withdrawal.user == address(0)) {
            revert WithdrawalDoesNotExist();
        }

        if (withdrawal.user != msg.sender) {
            revert NotYourWithdrawal();
        }

        if (block.timestamp < withdrawal.unlockTime) {
            revert WithdrawalNotReady();
        }

        if (withdrawal.claimed) {
            revert WithdrawalAlreadyClaimed();
        }

```

**File:** contracts/KERNEL/KernelDepositPool.sol (L363-373)
```text
        withdrawal.claimed = true;

        // Remove the withdrawal ID from the user's list of withdrawal IDs
        uint256[] storage userWithdrawalIdsArray = userWithdrawalIds[msg.sender];
        for (uint256 i = 0; i < userWithdrawalIdsArray.length; ++i) {
            if (userWithdrawalIdsArray[i] == _withdrawalId) {
                userWithdrawalIdsArray[i] = userWithdrawalIdsArray[userWithdrawalIdsArray.length - 1];
                userWithdrawalIdsArray.pop();
                break;
            }
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L531-533)
```text
    function isWithdrawalClaimable(uint256 _withdrawalId) external view returns (bool) {
        return block.timestamp >= withdrawals[_withdrawalId].unlockTime;
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L540-542)
```text
    function isWithdrawalClaimed(uint256 _withdrawalId) external view returns (bool) {
        return withdrawals[_withdrawalId].claimed;
    }
```
