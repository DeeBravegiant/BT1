The code confirms the vulnerability is real. Here is the full analysis:

---

### Title
Admin Reducing `maxNumberOfWithdrawalsPerUser` Below a User's Current Open Withdrawal Count Temporarily Blocks New Withdrawals - ([File: contracts/KERNEL/KernelDepositPool.sol])

### Summary
`initiateWithdrawal` checks the user's current open withdrawal count against `maxNumberOfWithdrawalsPerUser` at call time. Because `setMaxNumberOfWithdrawalsPerUser` applies the new limit immediately with no floor relative to existing user state, a retroactive reduction can leave users unable to initiate new withdrawals until they claim enough existing ones.

### Finding Description
`initiateWithdrawal` enforces the limit with a strict `>=` check: [1](#0-0) 

`setMaxNumberOfWithdrawalsPerUser` only validates that the new value is in `(0, MAX_WITHDRAWALS_PER_USER]` and applies it immediately: [2](#0-1) 

There is no check that the new limit is `>=` any user's current open withdrawal count. If a user has `N` open withdrawals and the admin sets the limit to `M < N`, every subsequent call to `initiateWithdrawal` by that user reverts with `WithdrawalLimitReached` until they claim enough withdrawals to bring their count below `M`. Their remaining staked balance (not yet in a withdrawal queue) is inaccessible in the interim.

### Impact Explanation
The user's staked tokens that have not yet been queued for withdrawal are temporarily frozen. The user cannot exit those positions until they claim a sufficient number of existing withdrawals — which may themselves be subject to a non-zero `withdrawalDelay` (up to `MAX_WITHDRAWAL_DELAY = 30 days`). [3](#0-2) 

No funds are permanently lost, matching the scoped impact: **Low — contract fails to deliver promised returns, but doesn't lose value** (and potentially **Medium — temporary freezing of funds** if the withdrawal delay is long).

### Likelihood Explanation
This requires only a routine, permissioned admin call with a valid parameter. No key compromise or malicious intent is necessary — a well-intentioned admin reducing the limit for operational reasons (e.g., gas cost concerns, spam prevention) triggers the condition automatically for any user already at or above the new limit.

### Recommendation
In `setMaxNumberOfWithdrawalsPerUser`, either:
1. Enforce that the new limit is applied only to future users (not retroactively), or
2. Add a grace period / emit a warning event before the new limit takes effect, or
3. Change the `initiateWithdrawal` check to use the **minimum** of the user's count at the time they staked vs. the current limit, or most simply:
4. Change the check in `initiateWithdrawal` to `>` instead of `>=` (though this alone does not fully solve the retroactive reduction problem — the real fix is to not allow the new limit to be set below the current maximum open count of any user, or to track the limit per-user at stake time).

### Proof of Concept

```solidity
// 1. User stakes tokens and initiates 5 withdrawals
for (uint i = 0; i < 5; i++) {
    pool.initiateWithdrawal(1e18);
}
// userWithdrawalIds[user].length == 5

// 2. Admin reduces limit to 3
pool.setMaxNumberOfWithdrawalsPerUser(3); // valid: 3 > 0 && 3 <= 100

// 3. User tries to initiate another withdrawal for remaining staked balance
// Reverts: userWithdrawalIds[user].length (5) >= maxNumberOfWithdrawalsPerUser (3)
vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
pool.initiateWithdrawal(1e18); // BLOCKED

// 4. Remaining staked tokens are frozen until user claims 3 existing withdrawals
// (each subject to withdrawalDelay, up to 30 days)
``` [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L35-38)
```text
    uint256 public constant MAX_WITHDRAWAL_DELAY = 30 days;

    /// @notice The maximum number of open (unclaimed) withdrawals allowed per user at any time
    uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-338)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;

        // Create a withdrawal record
        uint256 withdrawalId = ++withdrawalCounter;
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
        userWithdrawalIds[msg.sender].push(withdrawalId);

        emit WithdrawalInitiated(msg.sender, _amount, withdrawalId, unlockTime);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L610-619)
```text
    function setMaxNumberOfWithdrawalsPerUser(uint256 _maxNumberOfWithdrawalsPerUser)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }

        maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
        emit MaxNumberOfWithdrawalsPerUserUpdated(_maxNumberOfWithdrawalsPerUser);
```
