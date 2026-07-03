The code confirms all factual claims in the report. Let me verify the key details:

- `maxNumberOfWithdrawalsPerUser` is declared at line 108 with no default value [1](#0-0) 
- `initialize()` never sets it (lines 259–271) [2](#0-1) 
- `initiateWithdrawal()` checks `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser` at line 323, which evaluates to `0 >= 0 = true` for every fresh caller [3](#0-2) 
- `setMaxNumberOfWithdrawalsPerUser()` is the only remedy and is admin-gated [4](#0-3) 

The vulnerability is real. However, the claimed severity (Critical — Permanent freezing) is overstated: the admin can call `setMaxNumberOfWithdrawalsPerUser()` at any time to unblock withdrawals, making the freeze **temporary**, not permanent. The correct impact is **Medium — Temporary freezing of funds**.

---

Audit Report

## Title
`maxNumberOfWithdrawalsPerUser` Uninitialized in `initialize()` Blocks All Withdrawals Until Admin Intervenes - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal()` reverts with `WithdrawalLimitReached` whenever `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, the condition `0 >= 0` is immediately true for every caller, blocking all withdrawals from the moment of deployment. Any user who stakes before the admin calls `setMaxNumberOfWithdrawalsPerUser()` cannot recover their tokens through any user-accessible path until that admin action is taken.

## Finding Description
`initialize()` sets only `kernelToken`, `rewardsToken`, and the admin role; `maxNumberOfWithdrawalsPerUser` is left at its zero default:

```solidity
// KernelDepositPool.sol L259–271
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    // ... address checks, __AccessControl_init, __ReentrancyGuard_init ...
    _setupRole(DEFAULT_ADMIN_ROLE, _admin);
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    // maxNumberOfWithdrawalsPerUser never assigned → remains 0
}
```

`initiateWithdrawal()` enforces the limit before creating any record:

```solidity
// L323
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

With `maxNumberOfWithdrawalsPerUser == 0`, a fresh user has `userWithdrawalIds[msg.sender].length == 0`, so `0 >= 0` is `true` and the function always reverts. There is no alternative withdrawal path: `claimWithdrawal()` requires a prior successful `initiateWithdrawal()`, and no direct `withdraw()` function exists. The only resolution is the admin calling `setMaxNumberOfWithdrawalsPerUser()` with a value in `[1, 100]`.

## Impact Explanation
**Medium — Temporary freezing of funds.** All staked KERNEL tokens are inaccessible to users through any public call until the admin sets `maxNumberOfWithdrawalsPerUser` to a non-zero value. The freeze is not permanent because the admin can unblock withdrawals at any time via `setMaxNumberOfWithdrawalsPerUser()`, but until that call is made, no user can recover staked tokens regardless of their balance.

## Likelihood Explanation
The contract is immediately usable after deployment: `stake()` imposes no precondition that `maxNumberOfWithdrawalsPerUser` is configured. Any user who stakes in the window between deployment and the admin's configuration call will find their funds frozen. Because `initialize()` is the canonical and only setup entry point and it omits this field, every fresh deployment starts in the broken state. No attacker action is required; the misconfiguration is self-inflicted by the deployment.

## Recommendation
Set `maxNumberOfWithdrawalsPerUser` to a safe non-zero default inside `initialize()`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    // ... existing checks ...
    maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
    withdrawalDelay = <sensible_default>;                     // also currently unset
}
```

Additionally, consider adding a guard in `stake()` that reverts if `maxNumberOfWithdrawalsPerUser == 0`, preventing deposits into a contract where withdrawals are disabled.

## Proof of Concept
```solidity
// 1. Deploy KernelDepositPool; initialize() is called — maxNumberOfWithdrawalsPerUser == 0
// 2. User approves and stakes
kernelToken.approve(address(pool), 1e18);
pool.stake(1e18);                        // succeeds — no precondition on maxNumberOfWithdrawalsPerUser

// 3. User tries to withdraw
pool.initiateWithdrawal(1e18);
// REVERTS: WithdrawalLimitReached()
// Reason: userWithdrawalIds[user].length (0) >= maxNumberOfWithdrawalsPerUser (0) → true

// 4. No other user-accessible withdrawal function exists.
//    Funds remain frozen until admin calls:
pool.setMaxNumberOfWithdrawalsPerUser(100); // onlyRole(DEFAULT_ADMIN_ROLE)

// 5. After admin call, user can retry:
pool.initiateWithdrawal(1e18);             // now succeeds
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L107-108)
```text
    /// @notice The maximum number of withdrawals that any user can have open (unclaimed) at any time
    uint256 public maxNumberOfWithdrawalsPerUser;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-323)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
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
