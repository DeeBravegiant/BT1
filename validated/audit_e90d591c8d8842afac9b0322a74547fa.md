### Title
Merkle Leaf Hash Missing `address(this)` Binding Enables Cross-Contract Proof Replay — (`contracts/KERNEL/KernelMerkleDistributor.sol`, `contracts/utils/MerkleDistributor/MerkleDistributor.sol`, `contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

---

### Summary

Multiple Merkle distributor contracts compute leaf hashes without binding `address(this)`. This is the direct structural analog of the reported ERC-1271 raw-hash fallback: just as the OnChainLab fallback omits the verifying contract address from the hash, every distributor in this codebase omits the distributor contract address from the Merkle leaf. If the same Merkle root is ever set on two distributor instances — a realistic operational scenario given the protocol deploys multiple distributor contracts — a user can replay the same proof on both contracts and drain double their allocation.

---

### Finding Description

Three distributor contracts compute their Merkle leaf without including `address(this)`:

**`KernelMerkleDistributor._processClaim()`** (line 320):
```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) 

**`MerkleDistributor.claim()`** (line 120):
```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [2](#0-1) 

**`KernelTop100MerkleDistributor._verifyClaimProof()`** (line 293):
```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [3](#0-2) 

None of these leaf constructions include `address(this)`. The Merkle root is set by the owner via `setMerkleRoot()` in `KernelMerkleDistributor` and `MerkleDistributor`, and is fixed at initialization in `KernelTop100MerkleDistributor`. [4](#0-3) [5](#0-4) 

Because `KernelMerkleDistributor` and `MerkleDistributor` use an **identical** leaf encoding (`keccak256(abi.encodePacked(index, account, cumulativeAmount))`), a proof that is valid against one contract's root is cryptographically valid against any other contract that holds the same root. The claim-tracking state (`userClaims`) is per-contract, so claiming on contract A does not mark the user as claimed on contract B. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

A user who holds a valid `(index, account, cumulativeAmount, proof)` tuple for one distributor can submit the identical tuple to any other distributor instance that holds the same Merkle root. Each contract independently transfers tokens to the user. The user receives their allocation from every matching contract, draining KERNEL (or other distributed tokens) beyond their entitled share. The stolen tokens are yield/reward tokens held by the distributor contracts.

---

### Likelihood Explanation

**Medium.** The protocol already deploys at least three structurally distinct distributor contracts (`KernelMerkleDistributor`, `MerkleDistributor`, `KernelTop100MerkleDistributor`) and is designed to be deployed as upgradeable proxies, implying multiple live instances across reward epochs. The exploit requires the same Merkle root to appear on two contracts. This can occur:

1. **Operationally**: The off-chain tooling generates a root from a snapshot and the operator sets it on multiple distributor contracts (e.g., one for direct claims, one for staking), which is a plausible workflow given `KernelMerkleDistributor` has both `claim()` and `claimAndStake()` paths.
2. **Accidentally**: An operator re-uses a previously generated root on a newly deployed distributor for a different token.

No private key compromise or governance capture is required. The attacker is any user who is legitimately included in the Merkle tree.

---

### Recommendation

Include `address(this)` in every leaf hash so that a proof is cryptographically bound to the specific distributor contract:

```solidity
// KernelMerkleDistributor / MerkleDistributor
bytes32 node = keccak256(abi.encodePacked(address(this), index, account, cumulativeAmount));

// KernelTop100MerkleDistributor
bytes32 leaf = keccak256(abi.encodePacked(address(this), user, amount));
```

The off-chain Merkle tree generation must be updated to include the target contract address in each leaf accordingly. This mirrors the fix applied in the Molecule protocol: wrapping the hash with the verifying contract's address before verification.

---

### Proof of Concept

```solidity
// Assume two MerkleDistributor proxies: distA and distB
// Admin sets the same merkleRoot on both (same snapshot, different tokens or epochs)

// Alice is in the tree: (index=1, account=alice, cumulativeAmount=100e18)
// Proof: merkleProof[]

// Alice claims from distA — succeeds, receives 100e18 tokens from distA
distA.claim(1, alice, 100e18, merkleProof);

// Alice replays the IDENTICAL call on distB — also succeeds
// because the leaf keccak256(abi.encodePacked(1, alice, 100e18)) is identical
// and distB.userClaims[alice] is independent of distA.userClaims[alice]
distB.claim(1, alice, 100e18, merkleProof);

// Alice has now received 200e18 tokens total, double her allocation.
```

The `isClaimed` check in each contract only reads its own `userClaims` mapping, so the replay is not blocked. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L180-181)
```text
    mapping(address user => UserClaim userClaim) public userClaims;

```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L239-243)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L319-322)
```text
        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L63-63)
```text
    mapping(address user => UserClaim userClaim) public userClaims;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L90-94)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L292-295)
```text
        // Verify the merkle proof
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);

```
