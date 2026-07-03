Looking at the contract code carefully, I need to trace the exact execution path.

**Key observations from the contract:**

**`setMerkleRoot` (line 156-167):** Overwrites `currentMerkleRoot` entirely — no historical root is stored. [1](#0-0) 

**`claim` (line 121):** Proof is verified exclusively against `currentMerkleRoot` — there is no fallback to any prior root. [2](#0-1) 

**`claim` index guard (line 111):** Allows `index < currentIndex`, so a prior index (e.g., 1) is not rejected by the index check after a second root is set — but the proof still fails because it is verified against the new root. [3](#0-2) 

**Tracing the exact scenario:**

| Step | State | Result |
|---|---|---|
| `setMerkleRoot(R1)` | `currentMerkleRoot=R1`, `currentIndex=1` | R1 commits user A → X |
| User A does not claim | `userClaims[A] = {0, 0}` | — |
| `setMerkleRoot(R2)` | `currentMerkleRoot=R2`, `currentIndex=2` | R2 omits user A |
| `claim(1, A, X, proofR1)` | index check passes (1 ≤ 2), `isClaimed` passes | `verify(proofR1, R2, node)` → **InvalidMerkleProof** |
| `claim(2, A, X, proofR2)` | no valid proof exists in R2 | **InvalidMerkleProof** |

User A's entitlement is permanently unclaimable. There is no stored root history, no grace period, and no recovery path.

**Does this require admin compromise?**

The rejection criterion is "admin/operator *compromise*" (malicious actor, stolen keys). This scenario does **not** require a malicious owner — it can occur from a routine off-chain mistake: a bug in the Merkle tree generation script that accidentally omits a user, a recomputation that resets cumulative amounts, or a tooling error. The contract enforces **no on-chain invariant** that new roots must be cumulative supersets of prior roots. The structural absence of historical root storage is the root cause, not admin intent.

---

### Title
Permanent Freezing of Unclaimed Yield via Root Replacement Without Historical Root Preservation — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

### Summary
`setMerkleRoot()` unconditionally overwrites `currentMerkleRoot` with no record of prior roots. `claim()` verifies proofs only against `currentMerkleRoot`. Any user who has not yet claimed under root R1 and is omitted from (or assigned a lower `cumulativeAmount` in) a subsequent root R2 permanently loses their entitlement with no recovery path.

### Finding Description
`setMerkleRoot` stores only a single root:

```solidity
// line 161-164
currentMerkleRoot = _merkleRootToSet;
currentMerkleRootIndex++;
currentIndex++;
```

`claim` verifies exclusively against this single live root:

```solidity
// line 121
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
```

There is no mapping of `index → root`, no grace period, and no mechanism to claim against a superseded root. Once R2 replaces R1, a proof valid under R1 is permanently invalid.

The `index` guard (`index > currentIndex`) does not help: it allows historical indices to pass, but the proof still fails because it is checked against the current root, not the root that was active at that index. [4](#0-3) 

### Impact Explanation
Any user omitted from a new root, or whose `cumulativeAmount` is reduced in a new root, permanently loses all unclaimed yield committed under prior roots. The contract holds the token balance but provides no callable path to retrieve it. This matches **Medium — Permanent freezing of unclaimed yield**.

### Likelihood Explanation
Root updates are a routine operational event. Off-chain Merkle tree generation is error-prone: recomputation bugs, tooling changes, or snapshot timing issues can silently omit users. No on-chain guard prevents this. The likelihood is realistic and does not require malicious intent.

### Recommendation
Store a mapping from index to root:

```solidity
mapping(uint256 index => bytes32 root) public merkleRoots;
```

In `setMerkleRoot`, record `merkleRoots[currentIndex] = _merkleRootToSet`. In `claim`, verify the proof against `merkleRoots[index]` rather than `currentMerkleRoot`. This preserves the ability to claim against any historically committed root.

### Proof of Concept

```solidity
// Local test (no mainnet required)
// 1. Build tree1 with leaf: keccak256(abi.encodePacked(uint256(1), userA, uint256(100e18)))
// 2. Build tree2 with NO leaf for userA
// 3. distributor.setMerkleRoot(tree1.root);   // currentIndex = 1
// 4. // userA does NOT claim
// 5. distributor.setMerkleRoot(tree2.root);   // currentIndex = 2
// 6. vm.expectRevert(IMerkleDistributor.InvalidMerkleProof.selector);
//    distributor.claim(1, userA, 100e18, proofFromTree1);
//    // Fails: proof valid for tree1 but currentMerkleRoot is tree2.root
// 7. // No valid proof exists in tree2 for userA → permanently frozen
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L53-56)
```text
    uint256 public currentMerkleRootIndex;
    bytes32 public currentMerkleRoot;

    uint256 public currentIndex;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L111-113)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L119-123)
```text
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
