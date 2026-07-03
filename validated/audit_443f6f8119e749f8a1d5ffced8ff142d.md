### Title
Owner Can Set Malicious `kernelDepositPool` to Drain All Unclaimed KERNEL Rewards via Infinite Approval - (File: `contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`KernelMerkleDistributor.setKernelDepositPool()` allows the owner to swap the `kernelDepositPool` address at any time. On every such swap, the contract grants `type(uint256).max` ERC20 approval to the newly set address. Because the `KernelMerkleDistributor` holds the entire KERNEL token balance earmarked for user claims, a malicious owner can point `kernelDepositPool` to an attacker-controlled contract and immediately drain all unclaimed KERNEL rewards from the distributor — stealing yield that belongs to legitimate claimants.

---

### Finding Description

During initialization, `KernelMerkleDistributor` grants an infinite approval to the initial `kernelDepositPool`: [1](#0-0) 

The admin function `setKernelDepositPool()` revokes the old approval and immediately grants a fresh `type(uint256).max` approval to whatever address is supplied: [2](#0-1) 

There is no timelock, no check that the new address is a legitimate staking contract, and no requirement that all pending claims be settled first. The `KernelMerkleDistributor` custodies the full KERNEL token balance for all future claims: [3](#0-2) 

A malicious owner can therefore:
1. Deploy a contract `MaliciousPool` that implements `stakeFor()` as a no-op but exposes a `drain()` function.
2. Call `setKernelDepositPool(address(MaliciousPool))` — this immediately grants `MaliciousPool` unlimited spend authority over the distributor's KERNEL balance.
3. Call `MaliciousPool.drain()`, which executes `kernel.transferFrom(address(kernelMerkleDistributor), attacker, kernel.balanceOf(distributor))`.

All steps can occur in a single block. The attacker need not wait for any user interaction. The identical pattern exists in `KernelTop100MerkleDistributor.setKernelDepositPool()`: [4](#0-3) 

Additionally, `KernelTop100MerkleDistributor` exposes an even more direct vector — `withdrawTokens()` — which lets the owner transfer any token (including `kernel`) to an arbitrary recipient with no restrictions: [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

All KERNEL tokens held by `KernelMerkleDistributor` (and `KernelTop100MerkleDistributor`) represent rewards that users have earned but not yet claimed. A malicious owner can drain the entire balance in a single transaction, permanently destroying every user's pending KERNEL claim. The tokens are custodied by the distributor contract rather than per-user accounts, so one drain empties all outstanding entitlements simultaneously. [6](#0-5) 

---

### Likelihood Explanation

The attack requires the owner key to be malicious or compromised. However, the rug vector exists unconditionally in the deployed bytecode — there is no timelock, no governance delay, and no guard requiring zero pending claims before the swap. Any project or integration that relies on `KernelMerkleDistributor` implicitly trusts that the owner will never exercise this power. As with the FlywheelCore analog, the mere existence of this backdoor may negatively impact user trust and protocol reputation, and the trustworthiness of every future owner cannot be guaranteed. [7](#0-6) 

---

### Recommendation

1. **Make `kernelDepositPool` immutable**, or enforce that it can only be changed when the contract holds zero KERNEL tokens (i.e., all claims have been processed).
2. **Remove `withdrawTokens()`** from `KernelTop100MerkleDistributor`, or restrict it to tokens other than `kernel`.
3. **Place `setKernelDepositPool()` behind a timelock** so users can observe and react to a pending change before it takes effect.
4. **Revoke the infinite approval** pattern: instead of pre-approving `type(uint256).max`, approve only the exact amount needed per `claimAndStake()` call.

---

### Proof of Concept

```solidity
// Attacker deploys this contract
contract MaliciousPool {
    IERC20 kernel;
    address attacker;
    constructor(address _kernel, address _attacker) {
        kernel = IERC20(_kernel);
        attacker = _attacker;
    }
    // Satisfies the IKernelDepositPool interface
    function stakeFor(address, uint256) external {}
    // Drains the distributor
    function drain(address distributor) external {
        kernel.transferFrom(distributor, attacker, kernel.balanceOf(distributor));
    }
}

// Attack sequence (single block):
// 1. owner calls:
kernelMerkleDistributor.setKernelDepositPool(address(maliciousPool));
// → grants type(uint256).max approval to maliciousPool

// 2. attacker calls:
maliciousPool.drain(address(kernelMerkleDistributor));
// → transfers entire KERNEL balance to attacker
// All users' unclaimed KERNEL rewards are stolen.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L149-149)
```text
    IERC20 public kernel;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L224-226)
```text
        // Approve the KernelDepositPool contract to spend an unlimited amount of KERNEL tokens on behalf of this
        // contract
        kernel.forceApprove(_kernelDepositPool, type(uint256).max);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L261-263)
```text
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L270-284)
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
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L356-370)
```text
    function setKernelDepositPool(address _kernelDepositPool) external onlyOwner {
        UtilLib.checkNonZeroAddress(_kernelDepositPool);

        address oldKernelDepositPool = address(kernelDepositPool);
        kernelDepositPool = IKernelDepositPool(_kernelDepositPool);

        // Revoke the approval of the old KernelDepositPool contract to spend KERNEL tokens on behalf of this contract
        kernel.forceApprove(oldKernelDepositPool, 0);

        // Approve the KernelDepositPool contract to spend an unlimited amount of KERNEL tokens on behalf of this
        // contract
        kernel.forceApprove(_kernelDepositPool, type(uint256).max);

        emit KernelDepositPoolUpdated(_kernelDepositPool);
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L398-410)
```text
    function setKernelDepositPool(address _kernelDepositPool) external onlyOwner {
        UtilLib.checkNonZeroAddress(_kernelDepositPool);

        address oldKernelDepositPool = address(kernelDepositPool);

        // Revoke old approval and set new one
        kernel.forceApprove(oldKernelDepositPool, 0);
        kernel.forceApprove(_kernelDepositPool, type(uint256).max);

        kernelDepositPool = IKernelDepositPool(_kernelDepositPool);

        emit KernelDepositPoolUpdated(_kernelDepositPool);
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-471)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);

        emit TokensWithdrawn(_token, _amount, _recipient);
```
