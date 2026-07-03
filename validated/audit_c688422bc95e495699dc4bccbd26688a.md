### Title
`KernelMerkleDistributor` Verifies All Claims Against Only the Latest Merkle Root, Permanently Freezing Unclaimed Yield for Users With Valid Proofs From Prior Roots - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

### Summary
`KernelMerkleDistributor` maintains a cumulative `currentIndex` that grows with every `setMerkleRoot()` call, yet proof verification is always performed against the single latest `currentMerkleRoot`. A user holding a valid proof for an older root index can pass the index-range check (`index <= currentIndex`) but will always fail the Merkle proof verification, permanently freezing their unclaimed KERNEL yield.

### Finding Description
Each call to `setMerkleRoot()` increments both `currentMerkleRootIndex` and `currentIndex` and overwrites `currentMerkleRoot` with the new root:

```solidity
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    currentMerkleRoot = _merkleRootToSet;
    currentMerkleRootIndex++;
    currentIndex++;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
``` [1](#0-0) 

Inside `_processClaim()`, the index-range guard allows any past index:

```solidity
if (index == 0 || index > currentIndex) {
    revert InvalidIndex();
}
``` [2](#0-1) 

But the Merkle proof is verified exclusively against the current (latest) root:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
``` [3](#0-2) 

A proof generated for root N encodes `index = N` in its leaf. After root N+1 is published, `currentIndex` becomes N+1, so `index = N` still passes the range check. However, the leaf `keccak256(N, account, amount)` does not exist in root N+1's tree, so `MerkleProofUpgradeable.verify` returns `false` and the call reverts with `InvalidMerkleProof`. The user has no recourse: their old proof is permanently invalid against the new root, and if the new root does not re-include them, no valid proof for their allocation exists.

This is structurally identical to the reported WheelOfGuantune bug: a cumulative counter (`currentIndex`, analogous to `totalSegments`) grows across multiple configuration calls, but the data-lookup step (`currentMerkleRoot`, analogous to `rewardIdOfSegment[length-1]`) only covers the latest snapshot, leaving valid indices from prior rounds unreachable.

### Impact Explanation
Any user who received a valid KERNEL allocation in root N but did not claim before root N+1 was published will have their tokens permanently frozen inside the contract. The `claimableAmount` they are owed can never be extracted because no proof path exists from their old leaf to the new root. This constitutes **permanent freezing of unclaimed yield** (KERNEL tokens).

### Likelihood Explanation
`setMerkleRoot()` is a routine operational action called periodically to distribute new reward epochs. Every epoch boundary creates a window during which unclaimed users from the prior epoch lose access to their funds. Given that KERNEL distributions are time-sensitive and users may not monitor every root update, this scenario is realistically triggered in normal protocol operation.

### Recommendation
Store a mapping from each historical `currentMerkleRootIndex` to its corresponding root, and verify the proof against the root that was active when the claimed `index` was assigned:

```solidity
mapping(uint256 index => bytes32 root) public merkleRoots;

function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    currentMerkleRootIndex++;
    currentIndex++;
    currentMerkleRoot = _merkleRootToSet;
    merkleRoots[currentMerkleRootIndex] = _merkleRootToSet;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}

// In _processClaim:
bytes32 rootForIndex = merkleRoots[index];
if (!MerkleProofUpgradeable.verify(merkleProof, rootForIndex, node)) {
    revert InvalidMerkleProof();
}
```

This ensures that a proof generated for root N is always verified against root N, regardless of how many subsequent roots have been published.

### Proof of Concept
1. Owner calls `setMerkleRoot(root1)` → `currentIndex = 1`, `currentMerkleRoot = root1`.
2. Off-chain system distributes proof `(index=1, account=Alice, cumulativeAmount=100)` valid under `root1`.
3. Alice does not claim immediately.
4. Owner calls `setMerkleRoot(root2)` → `currentIndex = 2`, `currentMerkleRoot = root2`. Root 2 does not include Alice (or includes her with a different leaf structure).
5. Alice calls `claim(1, alice, 100, proof1)`.
   - `index = 1 <= currentIndex = 2` → passes `InvalidIndex` check.
   - `isClaimed(1, alice)` → `false` (never claimed).
   - `MerkleProofUpgradeable.verify(proof1, root2, leaf)` → **`false`** (proof is for `root1`, not `root2`).
   - Reverts with `InvalidMerkleProof`.
6. Alice's 100 KERNEL tokens are permanently frozen in the contract. [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-346)
```text
    function _processClaim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        internal
        returns (uint256)
    {
        UtilLib.checkNonZeroAddress(account);

        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

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

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim info
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
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
