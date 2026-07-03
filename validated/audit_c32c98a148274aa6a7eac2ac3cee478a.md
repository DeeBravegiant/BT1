### Title
Replacing `currentMerkleRoot` via `setMerkleRoot` Permanently Freezes Unclaimed KERNEL Yield for Prior-Root Claimants - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

---

### Summary

`KernelMerkleDistributor.setMerkleRoot` overwrites the single `currentMerkleRoot` storage slot and increments `currentIndex`. The `claim` / `claimAndStake` functions verify every proof exclusively against `currentMerkleRoot`. Any user who held a valid but unclaimed allocation under the previous root loses the ability to claim those KERNEL tokens once the root is replaced, because their old Merkle proof is cryptographically invalid against the new root and there is no path to claim against the old root.

The same pattern exists identically in `contracts/utils/MerkleDistributor/MerkleDistributor.sol`.

---

### Finding Description

`setMerkleRoot` is the only mechanism for updating the distribution root:

```solidity
// KernelMerkleDistributor.sol L402-L413
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    if (_merkleRootToSet == bytes32(0)) revert ZeroValueProvided();
    currentMerkleRoot = _merkleRootToSet;   // old root is gone
    currentMerkleRootIndex++;
    currentIndex++;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
``` [1](#0-0) 

The `_processClaim` internal function (called by both `claim` and `claimAndStake`) verifies the proof against the live `currentMerkleRoot` only:

```solidity
// KernelMerkleDistributor.sol L307-L322
if (index == 0 || index > currentIndex) revert InvalidIndex();
// ...
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node))
    revert InvalidMerkleProof();
``` [2](#0-1) 

There is no historical root registry, no fallback path to claim against a superseded root, and no on-chain invariant requiring the new root to be a cumulative superset of the old one. Once `setMerkleRoot` is called, the previous root is irrecoverably overwritten.

The identical vulnerability exists in `MerkleDistributor.sol`: [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Any user who was allocated KERNEL tokens in epoch N but did not claim before the owner calls `setMerkleRoot` for epoch N+1 permanently loses access to those tokens. The KERNEL balance remains locked in the distributor contract with no user-accessible withdrawal path. `KernelMerkleDistributor` has no `withdrawTokens` function, so the tokens are frozen unless the owner performs an upgrade.

**Impact: Medium — Permanent freezing of unclaimed yield.**

---

### Likelihood Explanation

Merkle roots are expected to be updated periodically (each new reward epoch). The protocol does not enforce that the new root is a cumulative superset of the old one. A routine root rotation — even one performed correctly from an off-chain perspective — can silently drop users who did not claim in time if the new root encodes a fresh (non-cumulative) snapshot. This is a realistic operational scenario, not a theoretical one.

---

### Recommendation

1. **Store historical roots**: Maintain a mapping `rootIndex => bytes32` and allow `claim` to accept a `rootIndex` parameter so users can prove against any past root.
2. **Enforce cumulative semantics on-chain**: Require that `cumulativeAmount` in the new root is always ≥ the user's previously recorded `userClaims[account].cumulativeAmount` before accepting a claim, and document that off-chain root generation must be strictly cumulative.
3. **Alternatively, mirror the Morpho suggestion**: Make the root settable only once per distributor deployment; deploy a new distributor contract for each epoch, ensuring the old contract remains claimable indefinitely.

---

### Proof of Concept

1. Owner calls `setMerkleRoot(root_A)` where root_A includes `(index=1, alice, cumulativeAmount=100e18)`. `currentIndex = 1`.
2. Alice does not claim.
3. Owner calls `setMerkleRoot(root_B)` where root_B is a fresh snapshot that omits Alice (or assigns her a different leaf). `currentIndex = 2`, `currentMerkleRoot = root_B`.
4. Alice calls `claim(1, alice, 100e18, proofForRootA)`.
   - `index (1) <= currentIndex (2)` → passes index check.
   - `isClaimed(1, alice)` → `false` (she never claimed).
   - `MerkleProofUpgradeable.verify(proofForRootA, root_B, node)` → **`false`** → reverts `InvalidMerkleProof`.
5. Alice has no alternative entry point. Her 100 KERNEL is permanently frozen in `KernelMerkleDistributor`. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L250-266)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        nonReentrant
        whenNotPaused
    {
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);

        emit Claimed(index, account, amountToSend);
    }
```

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
