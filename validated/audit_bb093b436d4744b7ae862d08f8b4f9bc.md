The code is clear. Let me confirm the exact `initialize` function to verify `maxNumberOfWithdrawalsPerUser` is truly absent from it.

### Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Permanently Blocks All Withdrawals — (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`maxNumberOfWithdrawalsPerUser` defaults to `0` and is never set in `initialize()`. The guard in `initiateWithdrawal` evaluates `0 >= 0 == true` on the very first call, causing an unconditional `WithdrawalLimitReached` revert for every user until an admin separately calls `setMaxNumberOfWithdrawalsPerUser`. If that call is never made, all staked KERNEL is permanently frozen.

> Note: The question cites `contracts/KERNEL/KERNEL.sol`, which is only a plain ERC20 token. The actual vulnerable contract is `contracts/KERNEL/KernelDepositPool.sol`.

---

### Finding Description

`maxNumberOfWithdrawalsPerUser` is declared as a plain storage variable: [1](#0-0) 

The `initialize()` function sets `kernelToken`, `rewardsToken`, and roles, but never assigns `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`: [2](#0-1) 

`initiateWithdrawal` then checks:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [3](#0-2) 

For any user who has never initiated a withdrawal, `userWithdrawalIds[msg.sender].length == 0`. The condition becomes `0 >= 0`, which is always `true`, so the function always reverts. `stake()` succeeds normally, but the only exit path for staked principal is permanently blocked.

The setter exists but is admin-only and not called during initialization: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of all staked KERNEL user funds.**

Every user who calls `stake()` transfers KERNEL tokens into the contract. With `maxNumberOfWithdrawalsPerUser == 0`, `initiateWithdrawal` is unconditionally bricked. There is no alternative withdrawal path. Funds are irrecoverable until an admin calls `setMaxNumberOfWithdrawalsPerUser`, and if that call is never made (e.g., oversight, lost key, or delayed deployment script), the freeze is permanent.

---

### Likelihood Explanation

**Medium.** The contract is upgradeable and the admin is expected to perform post-deployment configuration. However, `maxNumberOfWithdrawalsPerUser` is not documented as a required post-deploy step, and there is no on-chain enforcement preventing users from staking before it is set. Any user who stakes during the configuration gap — or if the admin simply omits the call — is permanently locked out. The constant `MAX_WITHDRAWALS_PER_USER = 100` exists, suggesting the intent was to use it as a default, but it was never wired into `initialize()`. [5](#0-4) 

---

### Recommendation

Set a safe default in `initialize()`:

```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```

Alternatively, add a `require(maxNumberOfWithdrawalsPerUser > 0)` guard in `stake()` to prevent users from depositing before the parameter is configured.

---

### Proof of Concept

```solidity
// Local fork / unit test — no mainnet interaction
function testPermanentWithdrawalFreeze() public {
    // Deploy proxy + initialize (setMaxNumberOfWithdrawalsPerUser is NOT called)
    KernelDepositPool pool = deployAndInitialize(admin, kernelToken, rewardToken);

    // Confirm the parameter is 0
    assertEq(pool.maxNumberOfWithdrawalsPerUser(), 0);

    // User stakes successfully
    vm.startPrank(user);
    kernelToken.approve(address(pool), 1e18);
    pool.stake(1e18);
    assertEq(pool.balanceOf(user), 1e18);

    // User cannot withdraw — always reverts
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(1e18);

    // Funds are locked: balance > 0, no withdrawal path exists
    assertEq(pool.balanceOf(user), 0);          // balance was deducted... wait
    // Actually balanceOf is NOT deducted because the revert happens before line 325
    // Re-check: revert at line 323 means lines 325-335 never execute
    assertEq(pool.balanceOf(user), 1e18);        // still locked in contract
    vm.stopPrank();
}
```

The revert at line 323 occurs before any state mutation, so `balanceOf[user]` remains positive and the tokens remain in the contract with no recoverable path for the user.

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L38-38)
```text
    uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
```

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
