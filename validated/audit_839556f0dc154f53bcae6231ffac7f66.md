Looking at the exact check in `initiateWithdrawal` and the default state of `maxNumberOfWithdrawalsPerUser`:

### Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Permanently Blocks All Withdrawals After `claimAndStake` - (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, and `0 >= 0` is always `true`, every call to `initiateWithdrawal` reverts with `WithdrawalLimitReached` for the lifetime of the contract if `setMaxNumberOfWithdrawalsPerUser` is never called. Meanwhile `stakeFor` (called by `KernelMerkleDistributor.claimAndStake`) has no such guard and succeeds, permanently trapping all tokens staked through that path.

---

### Finding Description

`KernelDepositPool.initialize()` sets only `kernelToken`, `rewardsToken`, and the admin role; `maxNumberOfWithdrawalsPerUser` is left at `0`. [1](#0-0) 

`initiateWithdrawal` enforces:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [2](#0-1) 

When `maxNumberOfWithdrawalsPerUser == 0`, the condition `array.length >= 0` is a tautology in Solidity (unsigned integers are always `>= 0`), so the revert fires unconditionally for every caller.

`stakeFor` contains no equivalent guard: [3](#0-2) 

`KernelMerkleDistributor.claimAndStake` calls `stakeFor` directly: [4](#0-3) 

The result: tokens are credited to `balanceOf[account]` and transferred into the pool, but the only exit path (`initiateWithdrawal`) is permanently sealed.

A critical internal contradiction reinforces that this is a code defect rather than a deployment choice: `setMaxNumberOfWithdrawalsPerUser` explicitly rejects `0` as an invalid value: [5](#0-4) 

The developers therefore acknowledge that `0` is an invalid operational state, yet `initialize` leaves the variable there with no enforcement to move it out.

---

### Impact Explanation

Any KERNEL tokens staked via `KernelMerkleDistributor.claimAndStake` (or `KernelDepositPool.stake` / `stakeFor`) while `maxNumberOfWithdrawalsPerUser == 0` are permanently unwithdrawable. There is no alternative exit function; `initiateWithdrawal` is the sole withdrawal entry point. The staked balance is locked in the contract with no recovery path available to users.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The precondition is that the admin deploys and initializes `KernelDepositPool` without subsequently calling `setMaxNumberOfWithdrawalsPerUser`. This is a realistic oversight because:

- `initialize` accepts no parameter for this value and gives no indication it must be set separately.
- The contract appears fully functional for staking immediately after `initialize`; the broken withdrawal path is not surfaced until a user attempts to withdraw.
- Deployment scripts or checklists that omit this step would silently produce a broken contract.

**Likelihood: Low** (requires an admin deployment oversight), but the consequence when it occurs is irreversible for all affected users.

---

### Recommendation

Set a safe non-zero default for `maxNumberOfWithdrawalsPerUser` inside `initialize`, or add a parameter to `initialize` that requires it to be provided and validated:

```solidity
function initialize(
    address _admin,
    address _kernelToken,
    address _rewardToken,
    uint256 _maxNumberOfWithdrawalsPerUser
) external initializer {
    // ... existing checks ...
    if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
        revert InvalidMaxNumberOfWithdrawalsPerUser();
    }
    maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
}
```

Additionally, add a guard in `stakeFor` (and `stake`) that reverts if `maxNumberOfWithdrawalsPerUser == 0`, preventing tokens from being locked in an unwithdrawable state.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Pseudocode unit test (Foundry style)
function test_claimAndStake_permanentFreeze() public {
    // 1. Deploy contracts WITHOUT calling setMaxNumberOfWithdrawalsPerUser
    KernelDepositPool pool = new KernelDepositPool();
    pool.initialize(admin, address(kernelToken), address(rewardToken));
    // maxNumberOfWithdrawalsPerUser == 0 at this point

    KernelMerkleDistributor distributor = new KernelMerkleDistributor();
    distributor.initialize(address(kernelToken), address(pool), treasury, 0);

    // 2. Grant STAKE_FOR_ROLE to distributor
    pool.grantRole(pool.STAKE_FOR_ROLE(), address(distributor));

    // 3. Fund distributor, set merkle root, user calls claimAndStake
    // stakeFor succeeds: balanceOf[user] += amount
    distributor.claimAndStake(index, user, amount, proof); // succeeds

    assertGt(pool.balanceOf(user), 0); // tokens are staked

    // 4. User tries to withdraw — always reverts
    vm.prank(user);
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(amount); // 0 >= 0 → always true → always reverts
}
```

The `stakeFor` call at step 3 succeeds because it has no `maxNumberOfWithdrawalsPerUser` guard. [3](#0-2)  The `initiateWithdrawal` call at step 4 always reverts because `0 >= 0` is always `true`. [2](#0-1)

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L296-314)
```text
    function stakeFor(
        address _account,
        uint256 _amount
    )
        external
        nonReentrant
        onlyRole(STAKE_FOR_ROLE)
        updateReward(_account)
    {
        UtilLib.checkNonZeroAddress(_account);

        if (_amount == 0) revert AmountZero();

        balanceOf[_account] += _amount;
        totalKernelStaked += _amount;
        kernelToken.safeTransferFrom(msg.sender, address(this), _amount);

        emit StakedFor(msg.sender, _account, _amount);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L323-323)
```text
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L614-616)
```text
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L280-284)
```text
        uint256 amountToStake = _processClaim(index, account, cumulativeAmount, merkleProof);

        IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake);

        emit ClaimedAndStaked(index, account, amountToStake);
```
