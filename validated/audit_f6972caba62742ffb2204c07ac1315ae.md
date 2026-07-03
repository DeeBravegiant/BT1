### Title
Single Mutable Merkle Root Invalidates Pending Claims on Root Update — (File: `contracts/KERNEL/KernelMerkleDistributor.sol`, `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary
`KernelMerkleDistributor` and `MerkleDistributor` verify reward claims against a single mutable `currentMerkleRoot`. When `setMerkleRoot` is called, the old root is **discarded entirely** and replaced. Any user who holds a valid off-chain proof for the old root but has not yet submitted their claim will find their proof permanently invalid against the new root. If the replacement root omits that user (an operationally realistic scenario during reward-period rollovers), their entitled KERNEL/token rewards are frozen in the contract with no on-chain recovery path.

---

### Finding Description

`setMerkleRoot` overwrites `currentMerkleRoot` in a single storage write:

```solidity
// KernelMerkleDistributor.sol
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    currentMerkleRoot = _merkleRootToSet;   // old root discarded
    currentMerkleRootIndex++;
    currentIndex++;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
``` [1](#0-0) 

The `_processClaim` internal function then verifies the user's proof exclusively against this single live root:

```solidity
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
``` [2](#0-1) 

No historical root registry exists. The `index` range check (`index > currentIndex`) does **not** prevent a user from submitting a proof for a prior index — it only bounds the index to the total number of roots ever set. But the proof is still verified against the **current** root, not the root that was active when `index` was assigned:

```solidity
if (index == 0 || index > currentIndex) {
    revert InvalidIndex();
}
``` [3](#0-2) 

The identical pattern exists in `MerkleDistributor`: [4](#0-3) [5](#0-4) 

This is the direct analog of the BeaconKit bug: BeaconKit used `ActiveForkVersionForEpoch(epoch)` — a mutable, currently-active value — for deposit signature verification instead of the fixed genesis fork version. Here, the protocol uses `currentMerkleRoot` — a mutable, currently-active value — for claim proof verification instead of the root that was active when the user's entitlement was established.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

A user who received a valid off-chain proof for root R₁ (index = N) but does not submit their claim before the owner calls `setMerkleRoot(R₂)` will:

1. Pass the `index > currentIndex` guard (N ≤ N+1).
2. Fail `MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)` because `currentMerkleRoot` is now R₂, not R₁.
3. Have no on-chain path to recover their tokens.

If the replacement root R₂ omits the user entirely (e.g., a new reward epoch that only covers new participants, or an off-chain bookkeeping error), the user's KERNEL tokens remain locked in the contract indefinitely. There is no `recoverTokens` or historical-root fallback function.

---

### Likelihood Explanation

**Medium.**

1. Root updates are a routine, expected operation — each new reward distribution period triggers a `setMerkleRoot` call.
2. Users do not know when the next root update will occur; they may hold a valid proof for days or weeks before claiming.
3. The cumulative-amount design means a well-operated system would carry forward unclaimed balances into each new root, but the contract enforces **no such invariant on-chain**. A single off-chain bookkeeping error or a deliberate omission during a reward-period rollover is sufficient to permanently freeze a user's yield.
4. The `MerkleDistributor` variant distributes arbitrary ERC-20 tokens, broadening the affected asset surface.

---

### Recommendation

Maintain a mapping from each historical index to its corresponding root, and verify the user's proof against the root that was active when their `index` was assigned:

```solidity
mapping(uint256 index => bytes32 root) public merkleRoots;

function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    currentIndex++;
    merkleRoots[currentIndex] = _merkleRootToSet;
    currentMerkleRoot = _merkleRootToSet;
    emit MerkleRootSet(currentIndex, _merkleRootToSet);
}

// In _processClaim:
bytes32 rootForIndex = merkleRoots[index];
if (!MerkleProofUpgradeable.verify(merkleProof, rootForIndex, node)) {
    revert InvalidMerkleProof();
}
```

This mirrors the consensus-spec fix: use the **fixed value that was in effect when the user's entitlement was established** (genesis fork version / the root at the time of the user's index assignment), not the mutable current value.

---

### Proof of Concept

1. Owner calls `setMerkleRoot(R₁)` → `currentIndex = 1`, `currentMerkleRoot = R₁`. User A is allocated 500 KERNEL in R₁ (cumulative).
2. User A receives their off-chain proof `(index=1, account=A, cumulativeAmount=500, proof=[...])` but does not claim immediately.
3. Owner calls `setMerkleRoot(R₂)` for a new reward epoch → `currentIndex = 2`, `currentMerkleRoot = R₂`. R₂ covers only new participants; User A is not included.
4. User A submits `claim(1, A, 500, proof)`:
   - `index=1 > currentIndex=2` → **false**, passes.
   - `isClaimed(1, A)` → `userClaims[A].lastClaimedIndex (0) >= 1` → **false**, passes.
   - `MerkleProofUpgradeable.verify(proof, R₂, node)` → **reverts `InvalidMerkleProof`** because the proof was generated for R₁.
5. User A cannot claim with `index=2` either, because they have no proof for R₂ and R₂ does not include them.
6. User A's 500 KERNEL are permanently frozen in `KernelMerkleDistributor`. [6](#0-5) [1](#0-0) [7](#0-6) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-323)
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L96-147)
```text
    /// @inheritdoc IMerkleDistributor
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        whenNotPaused
    {
        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

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

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim info, and send the token.
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);

        emit Claimed(index, account, claimableAmount);
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
