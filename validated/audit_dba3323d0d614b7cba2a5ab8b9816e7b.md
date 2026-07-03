Audit Report

## Title
Missing `msg.sender == account` Authorization in `claim()` Allows Anyone to Force-Claim on Behalf of Any User, Stealing Their Fee-Exempt Yield - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never verifies that `msg.sender == account`. Any unprivileged caller can reconstruct a valid Merkle proof from the public tree and trigger a claim for any user at the current `feeInBPS` rate, permanently consuming that user's claimable epoch and diverting the fee portion to `protocolTreasury`. The sister contract `KernelMerkleDistributor` correctly enforces caller identity, confirming the omission is a defect.

## Finding Description
`MerkleDistributor.claim()` performs three checks before executing: a non-zero root check, an index bounds check, and an `isClaimed` check. None of these verify that `msg.sender == account`. [1](#0-0) 

The Merkle proof only authenticates the tuple `(index, account, cumulativeAmount)` as a valid leaf — it does not authenticate the caller. [2](#0-1) 

After proof verification, the contract deducts a fee of up to 10% (`MAX_FEE_IN_BPS = 1000`) from the claimable amount, transfers the remainder to `account`, and sends the fee to `protocolTreasury`. The user's claim state is then permanently updated. [3](#0-2) 

By contrast, `KernelMerkleDistributor._processClaim()` explicitly enforces caller identity before any state mutation: [4](#0-3) 

## Impact Explanation
**High — Theft of unclaimed yield.**

A user's rational strategy may be to defer claiming until `feeInBPS` is reduced to zero (the owner can set it to 0 via `setFeeInBPS`). An attacker can front-run that window by force-claiming for the user at the current non-zero fee rate. The fee is irrecoverable: once `userClaims[account].cumulativeAmount` is updated, the user can never re-claim that epoch. The fee portion — up to 10% of the user's allocation — is permanently diverted to `protocolTreasury` rather than to the user. This matches the allowed impact: **High. Theft of unclaimed yield.** [5](#0-4) 

## Likelihood Explanation
**High.** The Merkle tree leaves are public (the root is set on-chain; the full tree is published off-chain for users to generate proofs). Any attacker can enumerate all leaves, build proofs for every user, and batch-call `claim` for all of them in a single block. No special privilege, capital, or oracle access is required. The only gate is `whenNotPaused`, which is the normal operating state. [6](#0-5) 

## Recommendation
Add a caller-identity check identical to the one in `KernelMerkleDistributor._processClaim()`, inserted immediately after the `isClaimed` guard and before any state mutation or token transfer:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

## Proof of Concept
1. Owner sets `feeInBPS = 500` (5%) and publishes a Merkle root where `alice` is entitled to `1000e18` tokens.
2. Alice intends to wait until the owner sets `feeInBPS = 0` before claiming.
3. Attacker observes the Merkle tree, reconstructs Alice's proof `(index=1, account=alice, cumulativeAmount=1000e18, proof=[...])`.
4. Attacker calls `MerkleDistributor.claim(1, alice, 1000e18, proof)` from any EOA.
5. Contract executes:
   - `fee = 1000e18 * 500 / 10_000 = 50e18`
   - `amountToSend = 950e18`
   - Transfers `950e18` to Alice, `50e18` to `protocolTreasury`
   - Sets `userClaims[alice].cumulativeAmount = 1000e18`
6. Alice can never reclaim the `50e18` fee — her epoch is permanently consumed. [7](#0-6)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-117)
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
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L119-123)
```text
        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L133-146)
```text
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
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
