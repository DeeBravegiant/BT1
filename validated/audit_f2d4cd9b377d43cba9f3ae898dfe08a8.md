### Title
`maxNumberOfWithdrawalsPerUser` Uninitialized to Zero Permanently Blocks All Withdrawal Initiations — (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

`KernelDepositPool.initiateWithdrawal()` guards against exceeding a per-user withdrawal slot limit. The limit variable `maxNumberOfWithdrawalsPerUser` is never set in `initialize()`, so it defaults to `0`. The guard condition `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser` evaluates to `0 >= 0 = true` for every caller from the moment of deployment, causing every call to `initiateWithdrawal()` to revert with `WithdrawalLimitReached`. No user can ever unstake their KERNEL tokens through the normal path.

---

### Finding Description

`KernelDepositPool` tracks how many pending (unclaimed) withdrawals each user has via the array `userWithdrawalIds[user]`. Before creating a new withdrawal record, `initiateWithdrawal()` enforces a cap:

```solidity
// contracts/KERNEL/KernelDepositPool.sol : 323
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [1](#0-0) 

The cap variable is declared as a plain storage slot:

```solidity
// contracts/KERNEL/KernelDepositPool.sol : 108
uint256 public maxNumberOfWithdrawalsPerUser;
``` [2](#0-1) 

The `initialize()` function sets `kernelToken`, `rewardsToken`, and the admin role, but **never assigns `maxNumberOfWithdrawalsPerUser`**:

```solidity
// contracts/KERNEL/KernelDepositPool.sol : 259-271
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    // maxNumberOfWithdrawalsPerUser is never set → remains 0
}
``` [3](#0-2) 

A constant `MAX_WITHDRAWALS_PER_USER = 100` exists in the contract but is never used to seed the state variable: [4](#0-3) 

Because `maxNumberOfWithdrawalsPerUser == 0`, the guard `length >= 0` is always `true` (any `uint256` satisfies `>= 0`), so every call to `initiateWithdrawal()` reverts immediately, regardless of how many open withdrawals the user actually has.

The analog to the external report is direct: in the reference bug, a counter (`activeProposalsNow`) was never decremented in a specific code path, causing it to fill up and block new proposals. Here, the cap is set to `0` from the start, producing the same observable outcome — the slot-limit check always fires and the lifecycle action (withdrawal initiation) is permanently blocked.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Users who have staked KERNEL tokens via `stake()` or `stakeFor()` cannot call `initiateWithdrawal()` to begin the unstaking process. Their staked balance is recorded in `balanceOf[user]` and `totalKernelStaked`, but the only exit path (`initiateWithdrawal` → `claimWithdrawal`) is gated behind the broken limit check. Funds are not lost, but they are inaccessible until an admin separately calls a setter to raise `maxNumberOfWithdrawalsPerUser` above zero — if such a setter exists and is called. [5](#0-4) 

---

### Likelihood Explanation

**High.** The condition triggers for every user on every call from the moment the contract is deployed. No special attacker capability is required; any ordinary staker calling `initiateWithdrawal()` will hit the revert. The only mitigation is an admin action that is not part of the deployment flow.

---

### Recommendation

Initialize `maxNumberOfWithdrawalsPerUser` to the intended cap inside `initialize()`:

```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```

Alternatively, if the value must be configurable post-deployment, ensure the setter is called atomically as part of the deployment script and add a non-zero check in `initialize()`.

---

### Proof of Concept

1. Deploy `KernelDepositPool` via its proxy and call `initialize(admin, kernelToken, rewardToken)`.
2. As any user, approve and call `stake(1e18)`. The call succeeds; `balanceOf[user] = 1e18`.
3. Call `initiateWithdrawal(1e18)`.
4. The check `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser` evaluates to `0 >= 0 → true`.
5. The transaction reverts with `WithdrawalLimitReached`.
6. The user's KERNEL tokens remain locked in the contract with no way to retrieve them until an admin raises the limit. [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L37-38)
```text
    /// @notice The maximum number of open (unclaimed) withdrawals allowed per user at any time
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
