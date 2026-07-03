### Title
Unauthorized Claim Execution Allows Any Caller to Force Fee Deduction on Behalf of Any User - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` address parameter but never verifies that `msg.sender == account`. Any unprivileged caller can trigger a claim for any user, forcing a fee deduction of up to 10% from the victim's entitled token allocation. The sister contract `KernelMerkleDistributor` explicitly guards against this with `if (account != msg.sender) revert Unauthorized()`, confirming the protocol is aware of the pattern but failed to apply it here.

### Finding Description

`MerkleDistributor.claim()` is a public, permissionless function that accepts `account` as a caller-supplied parameter:

```solidity
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
    ...
    uint256 fee = (claimableAmount * feeInBPS) / 10_000;
    uint256 amountToSend = claimableAmount - fee;

    IERC20(token).safeTransfer(account, amountToSend);
    IERC20(token).safeTransfer(protocolTreasury, fee);
    ...
}
```

There is no check that `msg.sender == account`. All inputs needed to call this function (`index`, `account`, `cumulativeAmount`, `merkleProof`) are either emitted in on-chain events or derivable from the publicly known merkle tree. An attacker can reconstruct a valid proof for any victim and call `claim()` on their behalf at any time. [1](#0-0) 

By contrast, `KernelMerkleDistributor._processClaim()` explicitly enforces caller identity:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [2](#0-1) 

`MerkleDistributor` is a standalone, production-deployed contract in the repository scope and is not a test or generated file.



### Impact Explanation

**High — Theft of unclaimed yield.**

`feeInBPS` is configurable up to `MAX_FEE_IN_BPS = 1000` (10%). [3](#0-2) 

When an attacker forces a claim for a victim, the fee is permanently deducted from the victim's entitled allocation and transferred to `protocolTreasury`. The victim receives `claimableAmount - fee` instead of `claimableAmount`. The lost fee is irrecoverable — the victim's `userClaims[account].cumulativeAmount` is updated to the full `cumulativeAmount`, so they can never re-claim the fee portion. [4](#0-3) 

A user who intended to wait for a fee reduction (the owner can call `setFeeInBPS` to lower the fee) or who intended to claim at a self-chosen time is permanently deprived of the fee portion of their yield.

### Likelihood Explanation

**High.** The attack requires no special privileges. All parameters needed to construct a valid call — `index`, `account`, `cumulativeAmount`, and `merkleProof` — are either emitted in `Claimed` events for other users or derivable from the publicly distributed merkle tree. The attacker pays only gas. The attack is repeatable for every user in the distribution and can be executed immediately after a new merkle root is set.

### Recommendation

Add a caller identity check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [5](#0-4) 

### Proof of Concept

1. Protocol deploys `MerkleDistributor` with `feeInBPS = 1000` (10%).
2. Owner sets a merkle root covering Alice with `cumulativeAmount = 1000e18` tokens.
3. Attacker obtains Alice's `(index, account=Alice, cumulativeAmount=1000e18, merkleProof)` from the public merkle tree or emitted events.
4. Attacker calls `MerkleDistributor.claim(index, Alice, 1000e18, merkleProof)`.
5. Contract computes `fee = 100e18`, transfers `900e18` to Alice and `100e18` to `protocolTreasury`.
6. Alice's `userClaims[Alice].cumulativeAmount` is set to `1000e18` — she can never reclaim the `100e18` fee.
7. Alice permanently loses 10% of her entitled token allocation without her consent. [6](#0-5)

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
