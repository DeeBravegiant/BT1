### Title
Uninitialized `withdrawalDelay` Allows Immediate Bypass of Withdrawal Lock in KernelDepositPool - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.initialize()` never sets `withdrawalDelay`, leaving it at its Solidity default of `0`. When `initiateWithdrawal()` computes `unlockTime = block.timestamp + withdrawalDelay`, the result equals `block.timestamp`. The guard in `claimWithdrawal()` — `if (block.timestamp < withdrawal.unlockTime)` — is immediately false, so any user can claim their staked KERNEL tokens in the very next block with no enforced wait, defeating the withdrawal delay mechanism entirely.

### Finding Description
`KernelDepositPool.initialize()` sets `kernelToken`, `rewardsToken`, and the admin role, but never assigns a value to `withdrawalDelay`. [1](#0-0) 

Because `withdrawalDelay` is a plain `uint256` storage variable, it defaults to `0`. [2](#0-1) 

`initiateWithdrawal()` computes the unlock timestamp as:
```solidity
uint256 unlockTime = block.timestamp + withdrawalDelay;
``` [3](#0-2) 

With `withdrawalDelay == 0`, `unlockTime` equals `block.timestamp` at the moment of initiation. The readiness check in `claimWithdrawal()` is:
```solidity
if (block.timestamp < withdrawal.unlockTime) {
    revert WithdrawalNotReady();
}
``` [4](#0-3) 

Since `block.timestamp` is never strictly less than itself, this condition is false immediately — the revert never fires. A user can call `initiateWithdrawal()` and `claimWithdrawal()` in consecutive blocks (or even the same block on chains with sub-second finality) with zero enforced delay.

The only other gate in `initiateWithdrawal()` is the per-user withdrawal cap:
```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [5](#0-4) 

`maxNumberOfWithdrawalsPerUser` also defaults to `0`, so until the admin calls `setMaxNumberOfWithdrawalsPerUser`, no withdrawal can be initiated at all. However, the realistic deployment sequence is: admin sets `maxNumberOfWithdrawalsPerUser` to enable withdrawals but omits `setWithdrawalDelay`. In that window — which may persist indefinitely — `withdrawalDelay` remains `0` and the delay check is trivially bypassed.

The setter `setWithdrawalDelay` enforces `_withdrawalDelay != 0`, confirming the protocol's intent that a non-zero delay must always be active: [6](#0-5) 

### Impact Explanation
The withdrawal delay is the sole mechanism preventing users from staking and immediately unstaking KERNEL tokens. With `withdrawalDelay == 0`, any staker can call `initiateWithdrawal()` followed immediately by `claimWithdrawal()`, recovering their tokens with no lock-up. The contract fails to deliver its promised withdrawal delay guarantee. No funds are permanently lost, placing this in the **Low** impact tier: *contract fails to deliver promised returns, but doesn't lose value*.

### Likelihood Explanation
The vulnerability is active from the moment `maxNumberOfWithdrawalsPerUser` is set to a non-zero value until `setWithdrawalDelay` is explicitly called. Any staker can trigger it permissionlessly with no special preconditions. The window is realistic because `initialize()` silently leaves `withdrawalDelay` at `0` with no on-chain enforcement requiring it to be set before withdrawals are enabled.

### Recommendation
Initialize `withdrawalDelay` to a safe non-zero default inside `initialize()`, and add a guard in `initiateWithdrawal()` that reverts if `withdrawalDelay == 0`:

```solidity
// In initialize():
withdrawalDelay = 7 days; // or a constructor parameter

// In initiateWithdrawal():
require(withdrawalDelay > 0, "Withdrawal delay not configured");
```

This mirrors the fix pattern from the reference report: require the prerequisite state to be non-zero before the guarded action is permitted.

### Proof of Concept
1. Admin deploys `KernelDepositPool` and calls `initialize(admin, kernelToken, rewardToken)`. `withdrawalDelay` is `0`.
2. Admin calls `setMaxNumberOfWithdrawalsPerUser(10)` to enable withdrawals, but does not call `setWithdrawalDelay`.
3. Attacker calls `stake(1000e18)`.
4. Attacker calls `initiateWithdrawal(1000e18)`. `unlockTime = block.timestamp + 0 = block.timestamp`.
5. In the next block (or same block), attacker calls `claimWithdrawal(withdrawalId)`. The check `block.timestamp < unlockTime` evaluates to `false` (equal or greater), so no revert occurs.
6. Attacker receives `1000e18` KERNEL tokens immediately, with zero enforced delay.

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L96-97)
```text
    uint256 public withdrawalDelay;

```

**File:** contracts/KERNEL/KernelDepositPool.sol (L259-271)
```text
    function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
        UtilLib.checkNonZeroAddress(_admin);
        UtilLib.checkNonZeroAddress(_kernelToken);
        UtilLib.checkNonZeroAddress(_rewardToken);

        __AccessControl_init();
        __ReentrancyGuard_init();

        _setupRole(DEFAULT_ADMIN_ROLE, _admin);

        kernelToken = IERC20(_kernelToken);
        rewardsToken = IERC20(_rewardToken);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L323-323)
```text
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L329-330)
```text
        uint256 withdrawalId = ++withdrawalCounter;
        uint256 unlockTime = block.timestamp + withdrawalDelay;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L355-357)
```text
        if (block.timestamp < withdrawal.unlockTime) {
            revert WithdrawalNotReady();
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L598-603)
```text
    function setWithdrawalDelay(uint256 _withdrawalDelay) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_withdrawalDelay == 0) revert InvalidWithdrawalDelay();
        if (_withdrawalDelay > MAX_WITHDRAWAL_DELAY) revert MaximumWithdrawalDelayExceeded();

        withdrawalDelay = _withdrawalDelay;
        emit WithdrawalDelayUpdated(_withdrawalDelay);
```
