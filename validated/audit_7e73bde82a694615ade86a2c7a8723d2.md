The vulnerability is real and traceable through unmodified production code. Here is the full analysis:

---

### Title
Merkle Root Overwrite Permanently Locks Unclaimed KERNEL Tokens With No Recovery Path - (`contracts/KERNEL/KernelMerkleDistributor.sol`)

### Summary
`setMerkleRoot` overwrites the single `currentMerkleRoot` storage slot on every call. Because `_processClaim` verifies proofs exclusively against `currentMerkleRoot` and no historical roots are stored, any user who has not yet claimed from epoch N loses their allocation permanently the moment the owner sets epoch N+1's root. No emergency-withdrawal or rescue function exists for affected users.

### Finding Description

`setMerkleRoot` unconditionally overwrites `currentMerkleRoot`: [1](#0-0) 

Every subsequent call to `claim` or `claimAndStake` routes through `_processClaim`, which verifies the caller's proof against the **current** (now overwritten) root: [2](#0-1) 

The index guard at line 307 does **not** protect the user — after two `setMerkleRoot` calls `currentIndex == 2`, so `index=1` passes the `index > currentIndex` check, but the proof still fails verification against `root2`: [3](#0-2) 

`isClaimed` is also irrelevant here — it returns `false` for the unclaimed user, so no double-claim guard saves them: [4](#0-3) 

The contract holds no mapping of historical roots, no per-epoch root archive, and no admin rescue/sweep function for stranded tokens. The entire contract's admin surface is: [5](#0-4) 

### Impact Explanation

KERNEL tokens pre-funded into the distributor for epoch N allocations are permanently locked once epoch N+1's root is set. There is no on-chain path for affected users to recover their tokens, and no admin function to sweep or redirect stranded funds. This constitutes **permanent freezing of funds** (Critical).

### Likelihood Explanation

This does **not** require a malicious or compromised owner. The owner is expected to call `setMerkleRoot` periodically — once per distribution epoch — as part of normal protocol operation. Any user who delays claiming (network congestion, UI unavailability, travel, unawareness) between two consecutive `setMerkleRoot` calls loses their allocation. No time-lock, grace period, or off-chain warning mechanism is enforced on-chain.

### Recommendation

1. **Store historical roots**: Replace the single `currentMerkleRoot` with a `mapping(uint256 => bytes32) public merkleRoots` keyed by `currentMerkleRootIndex`, and verify proofs against `merkleRoots[index]` rather than `currentMerkleRoot`.
2. **Enforce a claim window**: Add a minimum delay (e.g., 7 days) between consecutive `setMerkleRoot` calls via a `lastRootSetAt` timestamp check.
3. **Add an admin rescue path**: Provide a time-gated `rescueTokens` function that can only be called after a sufficiently long period, allowing recovery of genuinely unclaimed (not just delayed) allocations.

### Proof of Concept

```solidity
// 1. Deploy KernelMerkleDistributor, fund with 1000 KERNEL
// 2. Build root1 with leaf: keccak256(abi.encodePacked(uint256(1), user, uint256(500)))
owner.setMerkleRoot(root1);
// currentIndex == 1, currentMerkleRoot == root1

// 3. User does NOT claim yet

// 4. Build root2 with a different set of leaves (user not included)
owner.setMerkleRoot(root2);
// currentIndex == 2, currentMerkleRoot == root2

// 5. User attempts to claim epoch 1 allocation
// index=1 passes the `index > currentIndex` guard (1 <= 2)
// isClaimed returns false (never claimed)
// MerkleProofUpgradeable.verify(proof_for_root1, root2, node) → false
// → reverts InvalidMerkleProof

user.claim(1, user, 500, proof_for_root1); // REVERTS

// 6. Contract balance remains 1000 KERNEL
// User has no on-chain path to recover their 500 KERNEL allocation
assert(kernel.balanceOf(address(distributor)) == 1000e18);
```

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L239-243)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L307-309)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L319-323)
```text
        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L348-424)
```text
    /*//////////////////////////////////////////////////////////////
                            ADMIN FUNCTIONS
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
