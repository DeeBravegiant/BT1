Audit Report

## Title
`KernelMerkleDistributor.claimAndStake` permanently reverts due to missing `STAKE_FOR_ROLE` grant - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

## Summary

`KernelMerkleDistributor.claimAndStake` calls `KernelDepositPool.stakeFor`, which is gated by `onlyRole(STAKE_FOR_ROLE)`. Neither `KernelMerkleDistributor.initialize` nor `KernelDepositPool.initialize` ever grants `STAKE_FOR_ROLE` to `KernelMerkleDistributor`, and no `grantRole(STAKE_FOR_ROLE, ...)` call exists anywhere in the codebase. Every invocation of `claimAndStake` reverts unconditionally, making the function permanently non-functional at deployment.

## Finding Description

`KernelDepositPool` defines `STAKE_FOR_ROLE` at line 41 and enforces it on `stakeFor` via `onlyRole(STAKE_FOR_ROLE)` at line 302. `KernelDepositPool.initialize` only calls `_setupRole(DEFAULT_ADMIN_ROLE, _admin)` (line 267) — `STAKE_FOR_ROLE` is never pre-granted to any address.

`KernelMerkleDistributor.initialize` performs one wiring step: `kernel.forceApprove(_kernelDepositPool, type(uint256).max)` (line 226). It does not call `grantRole(STAKE_FOR_ROLE, address(this))` on `KernelDepositPool`, nor does `setKernelDepositPool` (lines 356–370). A codebase-wide search for `grantRole.*STAKE_FOR_ROLE` returns zero matches.

`claimAndStake` (lines 270–285) calls `IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake)` after `_processClaim` succeeds. Because `KernelMerkleDistributor` holds no `STAKE_FOR_ROLE`, the `onlyRole` check inside `stakeFor` reverts with `AccessControl: account 0x… is missing role 0x…`, rolling back the entire transaction including the `_processClaim` state updates.

## Impact Explanation

The `claimAndStake` function is a publicly advertised user-facing feature (dedicated `ClaimedAndStaked` event, `IKernelDepositPool.stakeFor` interface). It fails for every caller from the moment of deployment. No funds are lost — the transaction reverts atomically and users can still call `claim` — matching the **Low** impact class: *"Contract fails to deliver promised returns, but doesn't lose value."*

## Likelihood Explanation

The failure is deterministic and affects 100% of `claimAndStake` calls. Any unprivileged user who attempts to use the function will receive a revert. No special conditions, attacker capabilities, or external dependencies are required. The bug is present from deployment and persists until an admin separately grants `STAKE_FOR_ROLE` — a step that is neither documented nor enforced in code.

## Recommendation

Grant `STAKE_FOR_ROLE` to `KernelMerkleDistributor` on `KernelDepositPool` as part of the deployment sequence. After deploying both contracts, the deployer should call:

```solidity
kernelDepositPool.grantRole(STAKE_FOR_ROLE, address(kernelMerkleDistributor));
```

To make this atomic and non-forgettable, `KernelMerkleDistributor.initialize` could accept the `KernelDepositPool` admin as a parameter and call `grantRole` inline, or a deployment script should enforce this step before the contracts are considered live.

## Proof of Concept

1. Deploy `KernelDepositPool` with `initialize(deployer, kernelToken, rewardToken)` — only `DEFAULT_ADMIN_ROLE` is granted; `STAKE_FOR_ROLE` is unassigned.
2. Deploy `KernelMerkleDistributor` with `initialize(kernelToken, kernelDepositPool, treasury, fee)` — `forceApprove` runs, no role is granted.
3. Fund `KernelMerkleDistributor` with KERNEL tokens; call `setMerkleRoot` with a valid root.
4. Call `claimAndStake(index, account, cumulativeAmount, proof)` with a valid Merkle proof.
5. Execution reaches `KernelDepositPool.stakeFor`; `onlyRole(STAKE_FOR_ROLE)` reverts with `AccessControl: account <KernelMerkleDistributor> is missing role <STAKE_FOR_ROLE>`.
6. The entire transaction reverts; user claim state is unchanged, no tokens move.
7. Calling `claim` with the same proof succeeds, confirming the Merkle logic is correct and only the role wiring is absent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L41-41)
```text
    bytes32 public constant STAKE_FOR_ROLE = keccak256("STAKE_FOR_ROLE");
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L296-303)
```text
    function stakeFor(
        address _account,
        uint256 _amount
    )
        external
        nonReentrant
        onlyRole(STAKE_FOR_ROLE)
        updateReward(_account)
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L224-226)
```text
        // Approve the KernelDepositPool contract to spend an unlimited amount of KERNEL tokens on behalf of this
        // contract
        kernel.forceApprove(_kernelDepositPool, type(uint256).max);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L270-285)
```text
    function claimAndStake(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        nonReentrant
        whenNotPaused
    {
        uint256 amountToStake = _processClaim(index, account, cumulativeAmount, merkleProof);

        IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake);

        emit ClaimedAndStaked(index, account, amountToStake);
    }
```
