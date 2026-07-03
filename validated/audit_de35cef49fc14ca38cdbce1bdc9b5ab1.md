### Title
Merkle Root Replacement Without Historical Root Preservation Permanently Freezes Unclaimed KERNEL Yield — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`KernelMerkleDistributor.setMerkleRoot` overwrites `currentMerkleRoot` in place and discards the previous root. `_processClaim` verifies proofs exclusively against `currentMerkleRoot`. There is no storage of historical roots and no grace-period mechanism. If the owner sets a new root whose `cumulativeAmount` for a user is lower than the value in the superseded root (a realistic outcome of a slashing-adjusted off-chain reward recalculation), the user permanently loses the delta — it can never be claimed.

---

### Finding Description

`setMerkleRoot` performs a simple in-place replacement:

```solidity
// KernelMerkleDistributor.sol lines 402-413
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    if (_merkleRootToSet == bytes32(0)) revert ZeroValueProvided();
    currentMerkleRoot = _merkleRootToSet;   // old root is gone
    currentMerkleRootIndex++;
    currentIndex++;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
``` [1](#0-0) 

`_processClaim` then verifies the user's proof exclusively against the live root:

```solidity
// line 321
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
``` [2](#0-1) 

And computes the claimable delta:

```solidity
// line 326
uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
``` [3](#0-2) 

**Attack / failure path:**

| Step | State |
|------|-------|
| Root N set: Alice has `cumulativeAmount = 1000` at `index = N` | Alice generates proof P_N |
| EigenLayer slashing event triggers off-chain reward recalculation | Owner calls `setMerkleRoot(rootN1)` where Alice has `cumulativeAmount = 600` |
| Alice's pending tx (proof P_N) lands | `MerkleProofUpgradeable.verify(P_N, rootN1, node)` → `false` → `InvalidMerkleProof` |
| Alice re-generates proof P_N1 for root N+1 | She can only claim 600, losing 400 permanently |
| If Alice had already claimed 700 under a prior root | `600 - 700` underflows → Solidity 0.8 panic → she can claim **nothing** |

The contract has no mechanism to:
- Store or query historical roots
- Enforce that `cumulativeAmount` is non-decreasing across root updates
- Provide a claim window against the superseded root

`SlashingLib.sol` confirms that EigenLayer slashing can reduce withdrawable shares via `scaleForCompleteWithdrawal` / `calcSlashedAmount`, making a downward recalculation of KERNEL reward allocations a realistic off-chain event that would prompt a root update. [4](#0-3) 

---

### Impact Explanation

Permanent freezing of unclaimed KERNEL yield. Affected users cannot recover the yield that was valid under the superseded root. In the underflow case (user already claimed more than the new root grants), the user is also locked out of any future claims entirely, since every call to `_processClaim` will revert.

---

### Likelihood Explanation

The owner must publish a root where at least one user's `cumulativeAmount` is lower than in the previous root. This is not the normal case, but it is a realistic operational outcome when:

1. An EigenLayer slashing event causes the off-chain reward pipeline to retroactively reduce KERNEL allocations.
2. A bug in the off-chain Merkle tree generation produces incorrect (lower) cumulative values.
3. A root update races with a user's pending mempool transaction, and the new root simply omits that user (e.g., a partial re-generation covering only a subset of addresses).

No private-key compromise or malicious intent is required — the owner is acting within their intended role of updating the distribution root.

---

### Recommendation

1. **Store historical roots**: Map `rootIndex => merkleRoot` and allow `_processClaim` to accept a `rootIndex` parameter, verifying against the stored root for that index.
2. **Enforce non-decreasing `cumulativeAmount`**: Require that the new root's cumulative amounts are provably ≥ the previous root's amounts before accepting the update (or enforce this off-chain with a time-lock + monitoring).
3. **Grace period / pause before root rotation**: Pause claims, allow a settlement window, then rotate the root, ensuring no in-flight transactions are invalidated.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork-test skeleton (Foundry)
contract KernelMerkleDistributorRootRaceTest is Test {
    KernelMerkleDistributor distributor;
    address alice = address(0xA11CE);

    function testPermanentFreeze() public {
        // 1. Build root N: alice -> cumulativeAmount = 1000
        (bytes32 rootN, bytes32[] memory proofN, uint256 indexN) = _buildRoot(alice, 1000);
        vm.prank(owner);
        distributor.setMerkleRoot(rootN);

        // 2. Owner updates to root N+1: alice -> cumulativeAmount = 600 (slashing recalc)
        (bytes32 rootN1, bytes32[] memory proofN1, uint256 indexN1) = _buildRoot(alice, 600);
        vm.prank(owner);
        distributor.setMerkleRoot(rootN1);

        // 3. Alice's original tx (proof for root N) now fails
        vm.prank(alice);
        vm.expectRevert(IMerkleDistributor.InvalidMerkleProof.selector);
        distributor.claim(indexN, alice, 1000, proofN);

        // 4. Alice can only claim 600 under root N+1 — 400 KERNEL permanently frozen
        vm.prank(alice);
        distributor.claim(indexN1, alice, 600, proofN1);

        // 5. Assert: alice received 600, not 1000 — 400 is permanently unclaimable
        assertEq(kernel.balanceOf(alice), 600 * (10_000 - distributor.feeInBPS()) / 10_000);
    }
}
```

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L319-323)
```text
        // Verify the merkle proof
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

**File:** contracts/external/eigenlayer/libraries/SlashingLib.sol (L82-84)
```text
    function scaleForCompleteWithdrawal(uint256 scaledShares, uint256 slashingFactor) internal pure returns (uint256) {
        return scaledShares.mulWad(slashingFactor);
    }
```
