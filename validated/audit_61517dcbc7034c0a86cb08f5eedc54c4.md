The vulnerability is real and concrete. Let me confirm the exact lines:

- `initialize()` at line 259 never sets `maxNumberOfWithdrawalsPerUser`, so it defaults to `0`.
- `initiateWithdrawal()` at line 323 checks `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, which evaluates to `0 >= 0 = true` immediately for any new user, reverting with `WithdrawalLimitReached`.
- `setMaxNumberOfWithdrawalsPerUser()` at line 610 is a separate admin call that must be made post-deployment to unblock withdrawals.

The freezing is **temporary**, not permanent — the admin can call `setMaxNumberOfWithdrawalsPerUser` at any time to restore withdrawal functionality. The claimed "permanent" impact is overstated. The correct scoped impact is **Medium: Temporary freezing of funds**.

---

### Title
`maxNumberOfWithdrawalsPerUser` Defaults to Zero, Blocking All Withdrawals Until Admin Intervenes — (`contracts/KERNEL/KernelDepositPool.sol`)

### Summary
`KernelDepositPool.initialize()` does not set `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal()` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, and a fresh user's array length is also `0`, the condition `0 >= 0` is immediately `true` and every call reverts with `WithdrawalLimitReached`. All staked KERNEL tokens are frozen until the admin separately calls `setMaxNumberOfWithdrawalsPerUser`.

### Finding Description
`initialize()` sets only `kernelToken`, `rewardsToken`, and the admin role: [1](#0-0) 

`maxNumberOfWithdrawalsPerUser` is never assigned, so it remains `0`.

`initiateWithdrawal()` then checks: [2](#0-1) 

For any user whose `userWithdrawalIds` array is empty (length = 0), `0 >= 0` is `true`, and the function reverts unconditionally.

The only recovery path is the admin calling: [3](#0-2) 

There is also a constant `MAX_WITHDRAWALS_PER_USER = 100` defined: [4](#0-3) 

This constant is never used as a default in `initialize()`, making the omission a clear oversight.

### Impact Explanation
**Medium — Temporary freezing of funds.** Every user who stakes KERNEL tokens before the admin calls `setMaxNumberOfWithdrawalsPerUser` cannot initiate any withdrawal. Their tokens are locked in the contract for the duration of this misconfiguration window. The admin can unblock withdrawals at any time, so the freeze is not permanent, but it is a real, user-impacting denial of service on the withdrawal path.

### Likelihood Explanation
Likely in practice. The deployment sequence (deploy proxy → `initialize()` → users stake) is the natural order. Nothing in the deployment flow enforces or reminds the admin to call `setMaxNumberOfWithdrawalsPerUser` before users interact. Any user who stakes during this window is immediately affected.

### Recommendation
Set a safe default for `maxNumberOfWithdrawalsPerUser` inside `initialize()`, using the already-defined constant:

```solidity
// inside initialize()
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```

Alternatively, add a parameter to `initialize()` so the value is required at deployment time, preventing the zero-default state from ever being reachable.

### Proof of Concept
```solidity
// 1. Deploy proxy and call initialize(admin, kernelToken, rewardToken)
//    → maxNumberOfWithdrawalsPerUser == 0

// 2. User approves and stakes
kernelDepositPool.stake(1e18);
// → succeeds; balanceOf[user] = 1e18

// 3. User attempts to withdraw
kernelDepositPool.initiateWithdrawal(1e18);
// → REVERTS: WithdrawalLimitReached
//   because userWithdrawalIds[user].length (0) >= maxNumberOfWithdrawalsPerUser (0)

// 4. Admin fixes it
kernelDepositPool.setMaxNumberOfWithdrawalsPerUser(10);

// 5. User retries
kernelDepositPool.initiateWithdrawal(1e18);
// → succeeds
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L38-38)
```text
    uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
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
