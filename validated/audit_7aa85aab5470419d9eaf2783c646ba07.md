### Title
Arithmetic Underflow in `_processClaim` on Merkle Root Rollback Permanently Freezes User Yield — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`KernelMerkleDistributor._processClaim` computes `claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount` with no monotonicity guard. If the owner sets a new merkle root whose leaf for a previously-claiming user encodes a `cumulativeAmount` lower than the value already stored in `userClaims[account].cumulativeAmount`, Solidity 0.8 checked arithmetic causes an unconditional underflow revert. Because there is no admin function to reset `userClaims[account].cumulativeAmount`, every future claim call for that account will revert permanently.

---

### Finding Description

`setMerkleRoot` accepts any non-zero `bytes32` root without verifying that the new root is monotonically non-decreasing relative to previously stored user claim state: [1](#0-0) 

After a successful claim, the user's cumulative amount is persisted: [2](#0-1) 

On the next claim attempt, the subtraction at line 326 is performed with no underflow guard: [3](#0-2) 

If `cumulativeAmount` (from the new root) < `userClaims[account].cumulativeAmount` (from the prior claim), Solidity 0.8 reverts with an arithmetic panic. There is no `resetUserClaim`, `setUserClaim`, or any other admin function that could restore the user's state: [4](#0-3) 

The `isClaimed` guard does not prevent this path: after `setMerkleRoot`, `currentIndex` is incremented, so the new index is strictly greater than `userClaims[account].lastClaimedIndex`, and `isClaimed` returns `false`, allowing execution to reach the underflowing subtraction: [5](#0-4) 

---

### Impact Explanation

Every subsequent call to `claim` or `claimAndStake` for the affected account will revert with an arithmetic underflow panic. The user permanently loses access to all future KERNEL yield allocations from this distributor. There is no on-chain recovery path.

**Impact:** Medium — Permanent freezing of unclaimed yield.

---

### Likelihood Explanation

The trigger is an off-chain accounting error or an intentional root rollback by the owner. Merkle root rollbacks are a known operational risk in cumulative-amount distributor systems (e.g., correcting a miscalculation, reverting to a prior snapshot). The owner has unrestricted ability to call `setMerkleRoot` with any root. No attacker action is required; the freeze is a consequence of the missing contract-level invariant. Likelihood is low-to-medium given that root management errors do occur in practice, but the impact is irreversible once triggered.

---

### Recommendation

1. **Enforce monotonicity at claim time:** Before computing `claimableAmount`, assert `cumulativeAmount >= userClaims[account].cumulativeAmount` and revert with a descriptive error (e.g., `CumulativeAmountDecreased`) rather than relying on implicit underflow.

```solidity
if (cumulativeAmount < userClaims[account].cumulativeAmount) {
    revert CumulativeAmountDecreased();
}
uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

2. **Add an admin recovery function** that allows the owner to reset or correct `userClaims[account].cumulativeAmount` in the event of a root rollback, so affected users are not permanently locked out.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Pseudocode unit test (Foundry)
function test_underflow_on_root_rollback() public {
    // 1. Build root R1: user has cumulativeAmount = 100e18
    (bytes32 root1, uint256 idx1, bytes32[] memory proof1) = buildRoot(user, 100e18);
    distributor.setMerkleRoot(root1); // currentIndex = 1

    // 2. User claims from R1
    vm.prank(user);
    distributor.claim(idx1, user, 100e18, proof1);
    // userClaims[user].cumulativeAmount == 100e18

    // 3. Owner sets root R2 with cumulativeAmount = 50e18 (rollback / accounting error)
    (bytes32 root2, uint256 idx2, bytes32[] memory proof2) = buildRoot(user, 50e18);
    distributor.setMerkleRoot(root2); // currentIndex = 2

    // 4. User attempts to claim from R2
    vm.prank(user);
    vm.expectRevert(stdError.arithmeticError); // Solidity 0.8 underflow panic
    distributor.claim(idx2, user, 50e18, proof2);
    // 50e18 - 100e18 underflows → permanent revert, no recovery possible
}
```

The test demonstrates that after step 4, every future claim call with any `cumulativeAmount <= 100e18` will revert, permanently freezing the user's yield.

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L239-243)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L325-327)
```text
        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L334-335)
```text
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L350-424)
```text
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Sets the KernelDepositPool contract address
     * @param _kernelDepositPool The address of the new KernelDepositPool contract
     */
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

    /**
     * @notice Sets the protocol treasury address
     * @param _protocolTreasury The address of the new protocol treasury
     */
    function setProtocolTreasury(address _protocolTreasury) external onlyOwner {
        UtilLib.checkNonZeroAddress(_protocolTreasury);

        protocolTreasury = _protocolTreasury;

        emit ProtocolTreasuryUpdated(protocolTreasury);
    }

    /**
     * @notice Sets the fee in basis points
     * @param _feeInBPS The new fee in basis points
     */
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        feeInBPS = _feeInBPS;

        emit FeeInBPSUpdated(feeInBPS);
    }

    /**
     * @notice Sets the new merkle root
     * @param _merkleRootToSet The new merkle root to be set
     */
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }

    /// @dev Pauses the contract
    function pause() external onlyOwner {
        _pause();
    }

    /// @dev Unpauses the contract
    function unpause() external onlyOwner {
        _unpause();
    }
}
```
