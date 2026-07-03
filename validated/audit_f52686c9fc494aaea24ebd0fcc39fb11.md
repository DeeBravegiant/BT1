Audit Report

## Title
Incomplete Condition Check in `isWithdrawalClaimable()` Causes Misleading Claimability Signal - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`isWithdrawalClaimable()` only checks the time-lock condition, while `claimWithdrawal()` enforces three additional guards. Any caller using `isWithdrawalClaimable()` as a pre-flight check will receive a misleading `true` result for non-existent or already-claimed withdrawal IDs, causing the subsequent `claimWithdrawal()` call to revert.

## Finding Description
`isWithdrawalClaimable()` at L531-533 returns `block.timestamp >= withdrawals[_withdrawalId].unlockTime`. [1](#0-0) 

`claimWithdrawal()` enforces four guards before transferring tokens: existence check (`withdrawal.user == address(0)`), ownership check (`withdrawal.user != msg.sender`), time-lock check (`block.timestamp < withdrawal.unlockTime`), and claimed check (`withdrawal.claimed`). [2](#0-1) 

Only the third guard is mirrored in `isWithdrawalClaimable()`. This produces two concrete false-positive scenarios:

1. **Non-existent ID**: `withdrawals[nonExistentId].unlockTime` defaults to `0`. Since `block.timestamp >= 0` is always `true`, `isWithdrawalClaimable(nonExistentId)` returns `true` for any never-created ID, while `claimWithdrawal()` reverts with `WithdrawalDoesNotExist`.
2. **Already-claimed withdrawal**: After `claimWithdrawal()` sets `withdrawal.claimed = true`, the struct remains in storage with its original `unlockTime`. [3](#0-2)  `isWithdrawalClaimable()` still returns `true`, while `claimWithdrawal()` reverts with `WithdrawalAlreadyClaimed`.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.** The function is documented as returning "whether a withdrawal is ready to be claimed," but it returns `true` in cases where claiming will revert. No funds are lost, but the view function's contract promise is broken, causing wasted gas for any caller following the natural check-then-act pattern.

## Likelihood Explanation
The function is `external` and named as a pre-flight check. Any off-chain keeper, integrating contract, or front-end following the natural "check then act" pattern will hit this. The non-existent-ID false positive is trivially reachable by any caller passing an arbitrary `uint256`. The already-claimed false positive is reachable by any caller querying a previously claimed withdrawal ID.

## Recommendation
Align `isWithdrawalClaimable()` with all conditions enforced by `claimWithdrawal()`:

```solidity
function isWithdrawalClaimable(uint256 _withdrawalId) external view returns (bool) {
    Withdrawal storage w = withdrawals[_withdrawalId];
    return w.user != address(0)
        && !w.claimed
        && block.timestamp >= w.unlockTime;
}
```

Note: the `withdrawal.user != msg.sender` guard is intentionally omitted here since `isWithdrawalClaimable` is a general view function not tied to a specific caller.

## Proof of Concept
**Already-claimed false positive:**
1. Alice calls `initiateWithdrawal(100e18)`, receiving `withdrawalId = 5`.
2. After the delay, Alice calls `claimWithdrawal(5)` — succeeds; `withdrawal.claimed` is set to `true`. [3](#0-2) 
3. Any caller calls `isWithdrawalClaimable(5)` — returns `true` because `block.timestamp >= unlockTime` is still satisfied. [1](#0-0) 
4. Caller calls `claimWithdrawal(5)` — reverts with `WithdrawalAlreadyClaimed`. [4](#0-3) 

**Non-existent ID false positive:**
1. Any caller passes `_withdrawalId = 9999` (never created).
2. `withdrawals[9999].unlockTime == 0`, so `isWithdrawalClaimable(9999)` returns `true`.
3. `claimWithdrawal(9999)` reverts with `WithdrawalDoesNotExist`. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L347-361)
```text
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L363-363)
```text
        withdrawal.claimed = true;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L531-533)
```text
    function isWithdrawalClaimable(uint256 _withdrawalId) external view returns (bool) {
        return block.timestamp >= withdrawals[_withdrawalId].unlockTime;
    }
```
