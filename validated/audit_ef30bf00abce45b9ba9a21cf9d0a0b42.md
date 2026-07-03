### Title
Merkle Root Overwrite Permanently Freezes Unclaimed Yield for Prior-Period Claimants - (File: contracts/KERNEL/KernelMerkleDistributor.sol, contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary
Both `KernelMerkleDistributor` and `MerkleDistributor` store only a single `currentMerkleRoot`. When `setMerkleRoot` is called to begin a new distribution period, the previous root is permanently overwritten. Any user who holds a valid proof for the old root but has not yet claimed will find their proof cryptographically invalid against the new root, permanently freezing their unclaimed yield with no recovery path inside the contract.

---

### Finding Description

`setMerkleRoot` unconditionally replaces `currentMerkleRoot` and increments `currentIndex`:

```solidity
// KernelMerkleDistributor.sol lines 402–413
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    ...
    currentMerkleRoot = _merkleRootToSet;   // old root is gone
    currentMerkleRootIndex++;
    currentIndex++;
    ...
}
``` [1](#0-0) 

The identical pattern exists in `MerkleDistributor`: [2](#0-1) 

Proof verification in `_processClaim` always uses `currentMerkleRoot`:

```solidity
// KernelMerkleDistributor.sol lines 307–322
if (index == 0 || index > currentIndex) {
    revert InvalidIndex();
}
...
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
``` [3](#0-2) [4](#0-3) 

The index bound check (`index <= currentIndex`) permits a user to submit an old index (e.g., `index = N` when `currentIndex = N+1`), but the proof is still verified against the **new** root. A proof generated for root `N` is cryptographically incompatible with root `N+1`. The call reverts with `InvalidMerkleProof`, and there is no on-chain fallback to claim against a historical root.

No historical roots are stored anywhere in either contract. [5](#0-4) 

---

### Impact Explanation

Every user who has accrued but not yet claimed KERNEL tokens from a prior distribution period permanently loses those tokens the moment a new root is published. The tokens remain locked in the contract with no on-chain mechanism to recover them. This matches **Medium – Permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

`setMerkleRoot` is a routine operational call expected to be executed on a recurring basis (e.g., weekly/monthly reward epochs). Every root rotation is a trigger. Any user who misses the claim window between two consecutive root updates — due to inactivity, gas costs, UI unavailability, or simply not being notified — permanently loses their allocation. The probability compounds with each epoch.

---

### Recommendation

Store historical roots in a mapping keyed by index and verify the proof against the root that corresponds to the supplied `index`, not always the latest root:

```solidity
mapping(uint256 => bytes32) public merkleRoots;

function setMerkleRoot(bytes32 _root) external onlyOwner {
    currentIndex++;
    merkleRoots[currentIndex] = _root;
    currentMerkleRoot = _root;
}

// In _processClaim / claim:
bytes32 rootForIndex = merkleRoots[index];
if (!MerkleProofUpgradeable.verify(merkleProof, rootForIndex, node)) {
    revert InvalidMerkleProof();
}
```

This mirrors the "claim-only mode" recommendation from the reference report: old distribution periods remain claimable indefinitely even after new ones are added.

---

### Proof of Concept

1. Owner calls `setMerkleRoot(root1)` → `currentIndex = 1`, `currentMerkleRoot = root1`.
2. Alice earns 100 KERNEL in epoch 1. Off-chain system gives her a proof `P1` for `(index=1, alice, 100)` against `root1`.
3. Alice does not claim immediately.
4. Owner calls `setMerkleRoot(root2)` → `currentIndex = 2`, `currentMerkleRoot = root2`. `root1` is gone.
5. Alice calls `claim(index=1, alice, 100, P1)`.
   - Index check: `1 <= 2` → passes.
   - Proof check: `verify(P1, root2, node)` → **FAILS** (`InvalidMerkleProof`).
6. Alice cannot claim with `index=2` either because she has no proof for `root2` (and `root2` may not even include her epoch-1 allocation separately).
7. Alice's 100 KERNEL are permanently frozen in the contract. [6](#0-5) [7](#0-6)

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L307-323)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L111-123)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L156-167)
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
