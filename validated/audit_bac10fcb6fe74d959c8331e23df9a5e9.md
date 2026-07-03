Audit Report

## Title
`KernelMerkleDistributor.claimAndStake` permanently reverts due to missing `STAKE_FOR_ROLE` grant - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

## Summary

`KernelMerkleDistributor.claimAndStake` calls `KernelDepositPool.stakeFor`, which is gated by `onlyRole(STAKE_FOR_ROLE)`. Neither `KernelDepositPool.initialize` nor `KernelMerkleDistributor.initialize` ever grants `STAKE_FOR_ROLE` to `KernelMerkleDistributor`, and no other location in the codebase does so. Every invocation of `claimAndStake` reverts unconditionally, making the function permanently non-functional at deployment.

## Finding Description

`KernelDepositPool.initialize` grants only `DEFAULT_ADMIN_ROLE` and sets no other roles:

```solidity
// contracts/KERNEL/KernelDepositPool.sol L259-271
_setupRole(DEFAULT_ADMIN_ROLE, _admin);
```

`KernelDepositPool.stakeFor` enforces `onlyRole(STAKE_FOR_ROLE)` as its first access check:

```solidity
// contracts/KERNEL/KernelDepositPool.sol L296-302
function stakeFor(address _account, uint256 _amount)
    external
    nonReentrant
    onlyRole(STAKE_FOR_ROLE)   // ← reverts for any caller without this role
    updateReward(_account)
```

`KernelMerkleDistributor.initialize` wires the ERC-20 approval but never calls `grantRole`:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol L224-226
kernel.forceApprove(_kernelDepositPool, type(uint256).max);
// no grantRole(STAKE_FOR_ROLE, address(this)) call anywhere
```

`claimAndStake` delegates directly to `stakeFor`:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol L280-284
uint256 amountToStake = _processClaim(index, account, cumulativeAmount, merkleProof);
IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake);
emit ClaimedAndStaked(index, account, amountToStake);
```

A codebase-wide search for `grantRole` finds zero calls involving `STAKE_FOR_ROLE` in any project contract, deployment script, or initializer. The role is defined but never assigned, so the `onlyRole` check always reverts.

## Impact Explanation

This matches the allowed Low impact class: *"Contract fails to deliver promised returns, but doesn't lose value."* The `claimAndStake` function is a publicly advertised user-facing feature (dedicated `ClaimedAndStaked` event, explicit `IKernelDepositPool.stakeFor` interface). It fails for 100% of callers. No funds are lost because the transaction reverts atomically; users retain their unclaimed KERNEL and can use the separate `claim` function.

## Likelihood Explanation

The failure is deterministic and requires no special attacker capability. Any user who calls `claimAndStake` with a valid Merkle proof will receive an `AccessControl` revert. The condition persists from deployment until an admin separately grants `STAKE_FOR_ROLE` — a step that is absent from all initializers and undocumented.

## Recommendation

Grant `STAKE_FOR_ROLE` to `KernelMerkleDistributor` on `KernelDepositPool` as part of the deployment sequence. After deploying both contracts, the deployer must call:

```solidity
kernelDepositPool.grantRole(STAKE_FOR_ROLE, address(kernelMerkleDistributor));
```

To make this unforgettable, `KernelMerkleDistributor.initialize` (or `setKernelDepositPool`) should accept the `KernelDepositPool` admin as a parameter and call `grantRole` atomically, mirroring the existing `forceApprove` wiring.

## Proof of Concept

1. Deploy `KernelDepositPool` with `initialize(admin, kernelToken, rewardToken)` — only `DEFAULT_ADMIN_ROLE` is granted.
2. Deploy `KernelMerkleDistributor` with `initialize(kernel, kernelDepositPool, treasury, fee)` — `forceApprove` runs, no role is granted.
3. Fund `KernelMerkleDistributor` with KERNEL tokens; owner calls `setMerkleRoot` with a valid root.
4. Call `claimAndStake(index, account, cumulativeAmount, proof)` with a valid proof as `account`.
5. `_processClaim` succeeds and returns `amountToStake`.
6. `KernelDepositPool.stakeFor` is called; `onlyRole(STAKE_FOR_ROLE)` reverts with `AccessControl: account <KernelMerkleDistributor> is missing role <STAKE_FOR_ROLE>`.
7. Entire transaction reverts; user state is unchanged.
8. Calling `claim` with the same proof succeeds, confirming Merkle logic is correct and only the role assignment is missing.