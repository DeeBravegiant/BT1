### Title
Anyone Can Force-Claim Merkle Rewards for Any User, Extracting Protocol Fee From Their Yield - (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

### Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` address parameter but never verifies that `msg.sender == account`. Any external caller who possesses (or can reconstruct) a valid merkle proof for a victim can trigger the victim's claim, causing the protocol fee to be deducted from the victim's allocation immediately — even if the victim intended to wait for a fee reduction.

### Finding Description
In `contracts/utils/MerkleDistributor/MerkleDistributor.sol`, the public `claim()` function accepts `account` as a caller-supplied parameter and performs no authorization check tying `msg.sender` to `account`:

```solidity
function claim(
    uint256 index,
    address account,       // ← caller-supplied, never validated against msg.sender
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    ...
    IERC20(token).safeTransfer(account, amountToSend);
    IERC20(token).safeTransfer(protocolTreasury, fee);   // fee deducted from victim
    ...
}
```

The only validation is that the merkle proof is valid for the supplied `(index, account, cumulativeAmount)` tuple. Merkle proofs for all eligible addresses are published off-chain (API / IPFS) as part of the standard distribution flow, so any observer can obtain a victim's proof without any privileged access.

Compare this to `KernelMerkleDistributor._processClaim()`, which correctly enforces:
```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```
`MerkleDistributor` has no equivalent guard.

The fee is bounded by `MAX_FEE_IN_BPS = 1000` (10%) and is set by the owner. When `feeInBPS > 0`, an attacker who force-claims on behalf of a victim causes `fee = claimableAmount * feeInBPS / 10_000` tokens to be permanently diverted to `protocolTreasury` instead of the victim — yield the victim has not yet received and which is now unrecoverable.

### Impact Explanation
**High — Theft of unclaimed yield.**

When `feeInBPS > 0` (up to 10%), an attacker can force any eligible user's pending allocation to be claimed immediately. The fee portion — up to 10% of the victim's entire claimable balance — is permanently transferred to `protocolTreasury` rather than to the victim. The victim cannot reclaim it: the cumulative accounting (`userClaims[account].cumulativeAmount`) is updated atomically, so the fee-deducted amount is the final settlement. If the owner subsequently lowers `feeInBPS` (a normal operational action), victims who were force-claimed at the higher rate suffer a permanent, irreversible loss relative to what they would have received.

### Likelihood Explanation
**Medium.** The attacker requires only:
1. A valid merkle proof for the target account — these are distributed publicly off-chain as part of the standard claim UX.
2. Knowledge of the correct `index` and `cumulativeAmount` — both are derivable from the same public distribution data.
3. `feeInBPS > 0` — the contract supports fees up to 10% and the owner can set any value in `[1, 1000]`.

No privileged access, no flash loan, and no on-chain state manipulation is required. The attack is a single external call executable by any EOA.

### Recommendation
Add a caller authorization check identical to the one already present in `KernelMerkleDistributor`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, as `KernelTop100MerkleDistributor` does.

### Proof of Concept

1. Owner sets `feeInBPS = 500` (5%) and publishes a merkle root.
2. Alice is eligible for 1000 tokens. Her proof `(index=1, account=Alice, cumulativeAmount=1000, proof=[...])` is published in the public distribution API.
3. Attacker Bob calls:
   ```solidity
   merkleDistributor.claim(1, Alice, 1000, aliceProof);
   ```
4. The contract verifies the proof (valid), computes `fee = 1000 * 500 / 10000 = 50`, transfers 950 tokens to Alice and 50 tokens to `protocolTreasury`.
5. `userClaims[Alice].cumulativeAmount` is set to 1000.
6. Owner later reduces `feeInBPS` to 0. Alice can no longer claim the 50 tokens she lost — they are permanently in the treasury.
7. Bob spent only gas; Alice permanently lost 50 tokens of yield she was entitled to receive in full. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
