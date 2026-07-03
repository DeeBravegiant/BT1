### Title
Stale Merkle Root Overwrites Invalidate Prior Unclaimed Proofs — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

### Summary

`setMerkleRoot()` unconditionally overwrites `currentMerkleRoot` with no historical root storage. `claim()` verifies proofs exclusively against `currentMerkleRoot`. Any user holding a valid proof for a prior root who has not yet claimed before the next `setMerkleRoot()` call will have their proof permanently rejected by the verifier, even though the index-range guard (`index <= currentIndex`) still passes.

### Finding Description

`setMerkleRoot` stores only the latest root: [1](#0-0) 

`claim` verifies exclusively against that single stored root: [2](#0-1) 

The index guard only checks that the supplied index is within the range of roots ever published: [3](#0-2) 

**Concrete call sequence:**

1. Owner calls `setMerkleRoot(root1)` → `currentIndex = 1`, `currentMerkleRoot = root1`
2. Owner calls `setMerkleRoot(root2)` → `currentIndex = 2`, `currentMerkleRoot = root2`
3. User calls `claim(index=1, account, amount, proof_for_root1)`:
   - `index > currentIndex` → `1 > 2` → **passes**
   - `isClaimed(1, account)` → `0 >= 1` → **passes** (never claimed)
   - `MerkleProofUpgradeable.verify(proof_for_root1, root2, node)` → **reverts `InvalidMerkleProof`**

The leaf node encoding binds the proof to a specific `index`: [4](#0-3) 

So even if `root2` re-includes the user with the same cumulative amount, the proof is different (index=2 vs index=1), and the user must obtain a new off-chain proof. If `root2` omits the user entirely, they cannot claim at all until a future root re-includes them.

### Impact Explanation

Users with valid entitlements under a superseded root lose their claim window. Their tokens remain locked in the contract until the owner publishes a new root that re-includes them with a fresh cumulative amount and a new index. This constitutes **temporary freezing of funds** (Medium scope). In the worst case, if the owner never re-includes a user, the freeze becomes permanent.

### Likelihood Explanation

This does not require a malicious or compromised owner. It occurs through normal operation: the owner updates the root (e.g., to add new recipients, correct an error, or run a new distribution epoch) before all users from the prior epoch have claimed. Off-chain tooling or user latency makes this realistic. No front-running or brute force is required.

### Recommendation

- Store a mapping of `rootIndex => merkleRoot` so `claim` can verify against the root that was active when the index was assigned.
- Alternatively, enforce that `claim` accepts any index ≤ `currentIndex` and looks up the corresponding historical root.
- At minimum, document and enforce off-chain that a new root must include all unclaimed cumulative entitlements from prior roots, and that users must re-fetch proofs after each root update.

### Proof of Concept

```solidity
// 1. Owner sets root1 (index=1)
distributor.setMerkleRoot(root1);

// 2. Owner sets root2 before any user claims (index=2)
distributor.setMerkleRoot(root2);

// 3. User attempts to claim with valid proof for root1
// index=1 passes the range check (1 <= currentIndex=2)
// isClaimed returns false (never claimed)
// BUT verify(proof_for_root1, currentMerkleRoot=root2, node) → false
vm.expectRevert(IMerkleDistributor.InvalidMerkleProof.selector);
distributor.claim(1, user, amount, proofForRoot1);

// 4. Owner sets root3 that re-includes user at index=3
distributor.setMerkleRoot(root3);

// 5. User can now claim with proof for root3
distributor.claim(3, user, amount, proofForRoot3); // succeeds
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L111-113)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L120-123)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L161-164)
```text
        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;
```
