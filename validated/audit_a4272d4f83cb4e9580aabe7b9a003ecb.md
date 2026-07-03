### Title
Single `currentMerkleRoot` Slot Overwritten on Each Root Rotation Permanently Locks Prior-Round Unclaimed KERNEL Rewards - (File: `contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`KernelMerkleDistributor` stores only one active Merkle root in `currentMerkleRoot`. Every call to `setMerkleRoot` silently overwrites it. Any user who holds a valid proof for the previous root but has not yet called `claim` or `claimAndStake` will have their proof permanently invalidated, with no on-chain path to recover their KERNEL allocation.

---

### Finding Description

`setMerkleRoot` unconditionally replaces the single `currentMerkleRoot` storage variable and increments `currentIndex`:

```solidity
// KernelMerkleDistributor.sol lines 402-413
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    if (_merkleRootToSet == bytes32(0)) revert ZeroValueProvided();

    currentMerkleRoot = _merkleRootToSet;   // ← old root destroyed
    currentMerkleRootIndex++;
    currentIndex++;

    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
``` [1](#0-0) 

Inside `_processClaim`, proof verification is performed exclusively against the live `currentMerkleRoot`:

```solidity
// line 321
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
``` [2](#0-1) 

Once the root is rotated, a proof generated for the previous root will always fail this check. The `isClaimed` guard does not protect the user — it only prevents double-claiming, not the loss of an unclaimed allocation:

```solidity
// line 242
return userClaims[account].lastClaimedIndex >= index;
``` [3](#0-2) 

There is no `mapping(uint256 => bytes32)` of historical roots, so a user cannot retroactively prove membership in a prior distribution round. The contract provides no on-chain guarantee that the new root will carry forward unclaimed amounts from the old root.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

KERNEL tokens allocated to users in a prior round remain locked in the contract after root rotation. If the new root does not re-include those allocations (whether by operator error, off-chain tooling failure, or intentional omission), the tokens are permanently inaccessible to the affected users. The funds do not leave the contract, but no claimant path exists, making this a permanent freeze of unclaimed yield at minimum and outright theft of yield if the operator redistributes the balance.

---

### Likelihood Explanation

Root rotation is a routine operational action — it is the only mechanism to update distributions. Every rotation that occurs while any user has an unclaimed balance triggers the vulnerability. No special attacker action is required; the loss is a structural consequence of normal protocol operation.

---

### Recommendation

Replace the single `currentMerkleRoot` slot with a per-round mapping and verify proofs against the root for the specific round index supplied by the caller:

```solidity
mapping(uint256 => bytes32) public merkleRoots;

function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    if (_merkleRootToSet == bytes32(0)) revert ZeroValueProvided();
    currentIndex++;
    merkleRoots[currentIndex] = _merkleRootToSet;
    emit MerkleRootSet(currentIndex, _merkleRootToSet);
}
```

In `_processClaim`, verify against `merkleRoots[index]` instead of `currentMerkleRoot`. This allows users to claim from any historical round using the root that was active when their allocation was published.

---

### Proof of Concept

1. Owner calls `setMerkleRoot(root1)` → `currentIndex = 1`, `currentMerkleRoot = root1`.
2. Off-chain system publishes a proof for Alice: `(index=1, account=Alice, cumulativeAmount=500, proof_A)` valid under `root1`.
3. Alice does not claim immediately.
4. Owner calls `setMerkleRoot(root2)` → `currentIndex = 2`, `currentMerkleRoot = root2` (old `root1` is gone).
5. Alice calls `claim(1, Alice, 500, proof_A)`.
   - `index (1) <= currentIndex (2)` → passes the index guard.
   - `isClaimed(1, Alice)` → `lastClaimedIndex (0) >= 1` is false → passes.
   - `MerkleProofUpgradeable.verify(proof_A, root2, node)` → **false** → reverts `InvalidMerkleProof`.
6. Alice has no valid proof for `root2` unless the operator explicitly re-includes her allocation. If they do not, her 500 KERNEL tokens are permanently frozen in the contract. [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L160-167)
```text
    /// @notice The current index of the current merkle root
    uint256 public currentMerkleRootIndex;

    /// @notice The current merkle root
    bytes32 public currentMerkleRoot;

    /// @notice The current index
    uint256 public currentIndex;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L239-243)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
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
