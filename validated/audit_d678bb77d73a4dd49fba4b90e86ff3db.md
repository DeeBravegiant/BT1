### Title
`setMerkleRoot()` Overwrites Previous Root, Permanently Freezing Unclaimed KERNEL Yield - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

---

### Summary

`KernelMerkleDistributor.setMerkleRoot()` unconditionally overwrites the single `currentMerkleRoot` storage slot. Because `claim()` and `claimAndStake()` verify proofs exclusively against `currentMerkleRoot`, any user who has not yet claimed their KERNEL tokens from a prior root loses the ability to do so the moment a new root is posted. Their earned tokens remain permanently locked in the contract with no recovery path.

---

### Finding Description

`KernelMerkleDistributor` stores exactly one active merkle root at a time:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol
bytes32 public currentMerkleRoot;   // line 164
uint256 public currentIndex;        // line 167
```

The admin function `setMerkleRoot()` replaces this root unconditionally:

```solidity
// lines 402–413
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    if (_merkleRootToSet == bytes32(0)) revert ZeroValueProvided();

    currentMerkleRoot = _merkleRootToSet;   // old root is gone
    currentMerkleRootIndex++;
    currentIndex++;

    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
```

The internal claim processor `_processClaim()` validates every proof against the single live root:

```solidity
// lines 320–322
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
```

The index-validity gate allows a user to submit an old index (e.g., `index = 1` when `currentIndex = 2`):

```solidity
// line 307
if (index == 0 || index > currentIndex) revert InvalidIndex();
```

However, the proof for that old index was generated against the old root. After `setMerkleRoot()` is called, the proof is verified against the **new** root and will always revert with `InvalidMerkleProof`. There is no historical root registry, no fallback, and no admin rescue function. The KERNEL tokens allocated to unclaimed entries of the superseded root remain in the contract with no mechanism to distribute or recover them.

The identical pattern exists in `contracts/utils/MerkleDistributor/MerkleDistributor.sol` at lines 156–167 and 121–123.

---

### Impact Explanation

Every time the owner posts a new distribution root, all KERNEL tokens allocated to users who have not yet claimed under the previous root become permanently unclaimable. The tokens sit in the contract balance but can never be transferred to their rightful owners. This constitutes **permanent freezing of unclaimed yield**, matching the allowed impact scope at Medium severity.

---

### Likelihood Explanation

`setMerkleRoot()` is a routine operational call — it is expected to be invoked on a recurring schedule (e.g., weekly or monthly) to distribute new reward epochs. Users who are offline, unaware of the claim window, or simply slow to act will routinely miss the window between two consecutive root updates. No on-chain enforcement prevents the owner from posting a new root while unclaimed balances exist. The likelihood is **Medium** because it depends on the cadence of root updates relative to user claim activity, but it is a structural certainty that some users will be affected over time.

---

### Recommendation

Store a mapping of historical roots indexed by `currentMerkleRootIndex`, and allow `claim()` to accept a `rootIndex` parameter so users can prove against any previously posted root:

```solidity
mapping(uint256 => bytes32) public merkleRoots;

function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    currentMerkleRootIndex++;
    currentIndex++;
    merkleRoots[currentMerkleRootIndex] = _merkleRootToSet;
    currentMerkleRoot = _merkleRootToSet;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
```

Then verify the proof against `merkleRoots[rootIndex]` supplied by the caller, and ensure the `isClaimed` check is scoped per root index rather than globally. Alternatively, enforce a mandatory claim window before any new root can be posted, giving all users time to claim.

---

### Proof of Concept

1. Owner calls `setMerkleRoot(root1)` → `currentMerkleRoot = root1`, `currentIndex = 1`.
2. Alice earns 1000 KERNEL; her proof `P_alice` is generated against `root1` with `index = 1`.
3. Alice does not claim immediately.
4. Owner calls `setMerkleRoot(root2)` → `currentMerkleRoot = root2`, `currentIndex = 2`.
5. Alice calls `claim(1, alice, 1000, P_alice)`:
   - `index = 1 ≤ currentIndex = 2` → passes [1](#0-0) 
   - `isClaimed(1, alice)` → `lastClaimedIndex = 0 < 1` → passes [2](#0-1) 
   - `MerkleProofUpgradeable.verify(P_alice, root2, node)` → **reverts with `InvalidMerkleProof`** because `P_alice` was built for `root1` [3](#0-2) 
6. Alice's 1000 KERNEL are permanently locked in the contract. No recovery path exists. [4](#0-3) 

The same root-overwrite pattern is present in `MerkleDistributor.setMerkleRoot()` at line 156 and proof verification at line 121. [5](#0-4)

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L320-323)
```text
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
