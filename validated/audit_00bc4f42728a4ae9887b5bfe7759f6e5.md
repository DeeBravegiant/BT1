Looking at the actual contract code, I can now trace the exact execution path and evaluate the claim.

### Title
Non-Cumulative Merkle Tree Rotation Permanently Locks Unclaimed Rewards — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`KernelMerkleDistributor` stores only a single `currentMerkleRoot`. When `setMerkleRoot` is called, the old root is irrecoverably overwritten. The index-range guard in `_processClaim` (`index <= currentIndex`) implies that historical indices remain claimable after a root rotation, but proof verification is always performed against the single live root. If the off-chain tree is not constructed cumulatively, any user absent from the new tree permanently loses their prior-round rewards with no on-chain recovery path.

---

### Finding Description

**Root storage — single slot, no history:**

`currentMerkleRoot` is a single `bytes32` state variable. [1](#0-0) 

Each call to `setMerkleRoot` unconditionally overwrites it and increments `currentIndex`: [2](#0-1) 

**Index-range check creates a false invariant:**

`_processClaim` accepts any `index` in `[1, currentIndex]`: [3](#0-2) 

This implies that after root2 is set (`currentIndex = 2`), a user may still submit `index = 1`. However, proof verification is unconditionally performed against `currentMerkleRoot` (root2), not against the root that was active when index 1 was published: [4](#0-3) 

**Concrete failure path:**

| Step | Action | Result |
|------|--------|--------|
| 1 | Owner calls `setMerkleRoot(root1)` | `currentIndex = 1`, Alice included at index 1 with `amount = 100` |
| 2 | Alice does not claim | `userClaims[alice].lastClaimedIndex = 0` |
| 3 | Owner calls `setMerkleRoot(root2)` (non-cumulative, Alice absent) | `currentIndex = 2`, `currentMerkleRoot = root2` |
| 4 | Alice calls `claim(1, alice, 100, proof1)` | `InvalidMerkleProof` — proof1 does not verify against root2 |
| 5 | Alice calls `claim(2, alice, 100, proof2)` | `InvalidMerkleProof` — Alice has no leaf in tree2 |
| 6 | Alice's 100 KERNEL is permanently unclaimable | No recovery path exists on-chain |

The contract's `cumulativeAmount` accounting and the `claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount` subtraction [5](#0-4) 
both signal that cumulative tree construction is the intended design, yet the contract provides zero on-chain enforcement of this invariant and no NatSpec warning to operators.

---

### Impact Explanation

Affected users permanently lose their unclaimed KERNEL rewards. No principal (deposited assets) is at risk. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

The scenario does not require malicious intent. A routine operational mistake — regenerating the Merkle tree from only the current period's snapshot rather than a cumulative one — is sufficient. The contract provides no guard, no revert, and no warning to prevent this. The risk is elevated because:
- There is no on-chain check that the new root's tree is a superset of the old one.
- The index-range check actively misleads operators and integrators into believing historical indices remain claimable.

---

### Recommendation

1. **Store historical roots**: Replace `bytes32 public currentMerkleRoot` with `mapping(uint256 => bytes32) public merkleRoots` and verify proofs against `merkleRoots[index]` rather than the single live root.
2. **Or enforce cumulative construction**: If single-root storage is retained, remove the misleading index-range check and require `index == currentIndex` so the contract's interface accurately reflects that only the latest round is claimable.
3. **Document the invariant**: At minimum, add explicit NatSpec on `setMerkleRoot` stating that the new tree MUST include all prior recipients with their cumulative amounts.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

// Pseudocode — run against a local fork or Foundry test

function test_nonCumulativeRootLosesRewards() public {
    // Round 1: Alice is included
    bytes32 root1 = buildMerkleRoot([{index:1, account:alice, amount:100}]);
    distributor.setMerkleRoot(root1); // currentIndex = 1

    // Alice does NOT claim in round 1

    // Round 2: non-cumulative tree — Alice is absent
    bytes32 root2 = buildMerkleRoot([{index:2, account:bob, amount:200}]);
    distributor.setMerkleRoot(root2); // currentIndex = 2, root1 overwritten

    // Alice tries to claim round-1 rewards with her original proof
    vm.prank(alice);
    vm.expectRevert(InvalidMerkleProof.selector);
    distributor.claim(1, alice, 100, proof1AgainstRoot1);

    // Alice tries to claim at index 2 — she has no leaf
    vm.prank(alice);
    vm.expectRevert(InvalidMerkleProof.selector);
    distributor.claim(2, alice, 100, emptyProof);

    // Alice's 100 KERNEL is permanently locked in the contract
    assertEq(kernel.balanceOf(alice), 0);
}
```

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L163-167)
```text
    /// @notice The current merkle root
    bytes32 public currentMerkleRoot;

    /// @notice The current index
    uint256 public currentIndex;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L307-309)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L320-323)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L326-326)
```text
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L402-413)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```
