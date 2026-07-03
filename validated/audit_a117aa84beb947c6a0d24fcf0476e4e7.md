### Title
Missing Caller Identity Check Allows Anyone to Force Fee-Deducted Claims on Behalf of Any User - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never verifies that `msg.sender == account`. Any external caller can trigger a claim for any user, forcing the protocol fee to be deducted from that user's claimable token allocation. The sister contract `KernelMerkleDistributor` correctly guards against this with an explicit `Unauthorized` revert, but `MerkleDistributor` does not.

### Finding Description
In `contracts/utils/MerkleDistributor/MerkleDistributor.sol`, the `claim` function accepts `account` as a caller-supplied parameter and processes the full claim lifecycle — verifying the merkle proof, updating `userClaims[account]`, and transferring tokens — without ever checking that `msg.sender == account`:

```solidity
// MerkleDistributor.sol lines 97-147
function claim(
    uint256 index,
    address account,       // <-- caller-supplied, never validated against msg.sender
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    ...
    uint256 fee = (claimableAmount * feeInBPS) / 10_000;
    uint256 amountToSend = claimableAmount - fee;

    IERC20(token).safeTransfer(account, amountToSend);       // victim receives less
    IERC20(token).safeTransfer(protocolTreasury, fee);       // fee extracted
    ...
}
```

The merkle proof and all required parameters (`index`, `account`, `cumulativeAmount`) are deterministically derivable from on-chain events (`MerkleRootSet`) and the public merkle tree. An attacker can reconstruct any user's valid proof and call `claim` on their behalf before they do.

By contrast, `KernelMerkleDistributor._processClaim()` explicitly guards this path:
```solidity
// KernelMerkleDistributor.sol lines 311-313
if (account != msg.sender) {
    revert Unauthorized();
}
```

`MerkleDistributor` has no equivalent guard.

### Impact Explanation
**High — Theft of unclaimed yield.**

When an attacker forces a claim for a victim, the victim receives `claimableAmount - fee` tokens instead of `claimableAmount`. The fee (up to `MAX_FEE_IN_BPS = 1000`, i.e. 10%) is permanently redirected to `protocolTreasury`. The victim's `userClaims[account]` state is updated to reflect the forced claim, so the victim cannot reclaim the fee-deducted portion. The attacker pays only gas; the victim loses up to 10% of their entitled token distribution.

### Likelihood Explanation
**High.** The attack requires no privileged access, no private keys, and no oracle manipulation. All inputs needed to call `claim` for a victim (merkle index, account address, cumulative amount, merkle proof) are publicly available from on-chain events and the published merkle tree. Any external caller can execute this at any time while the contract is unpaused.

### Recommendation
Add a caller identity check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

### Proof of Concept
1. Protocol publishes a new merkle root via `setMerkleRoot`. The merkle tree is public; Alice's leaf is `keccak256(abi.encodePacked(index, alice, cumulativeAmount))`.
2. Attacker reconstructs Alice's valid `merkleProof` from the public tree.
3. Attacker calls `MerkleDistributor.claim(index, alice, cumulativeAmount, merkleProof)` before Alice does.
4. Contract verifies the proof (passes), deducts `fee = claimableAmount * feeInBPS / 10_000`, transfers `claimableAmount - fee` to Alice, and `fee` to `protocolTreasury`.
5. `userClaims[alice].lastClaimedIndex` is updated; Alice's claim is marked consumed.
6. Alice calls `claim` herself — reverts with `AlreadyClaimed`.
7. Alice has permanently lost up to 10% of her entitled token distribution. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-147)
```text
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
