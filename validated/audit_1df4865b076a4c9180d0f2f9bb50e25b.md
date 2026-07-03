### Title
Unprivileged Caller Can Force Fee-Bearing Claims on Behalf of Any Account - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter with no `msg.sender == account` check. Any public caller who possesses a valid Merkle proof for a victim can trigger that victim's claim, forcing the fee deduction (up to 10%) to be extracted from the victim's entitled tokens without the victim's consent.

### Finding Description
`MerkleDistributor.claim()` is a public, permissionless function that accepts `account` as a caller-supplied parameter:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol L97-L147
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

There is no guard of the form `if (account != msg.sender) revert Unauthorized()`. The sibling contract `KernelMerkleDistributor._processClaim()` correctly implements this guard at line 311:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol L311-L313
if (account != msg.sender) {
    revert Unauthorized();
}
```

`MerkleDistributor` is deployed in production for multiple live distributions: the KEP MerkleDistributor (`0x2DDB11443bD9Ceb92d4951A05f55eb7096EB53d3`), the EIGEN Programmatic MerkleDistributor (`0x9bB6d4b928645EdA8f9C019495695BA98969eFF1`), and the Scroll Airdrop MerkleDistributor (`0xbE7E2d809E2C7405B5972292986324a798921D98`). Merkle proofs for all eligible accounts are published off-chain (standard practice for airdrop distributions), making the attack data fully public.

### Impact Explanation
**High — Theft of unclaimed yield.**

The fee is capped at `MAX_FEE_IN_BPS = 1000` (10%). An attacker who forces a claim for a victim with, e.g., 10,000 tokens of entitlement causes 1,000 tokens to be permanently diverted to `protocolTreasury` instead of the victim. The victim receives only 9,000 tokens. The attacker does not receive the fee directly, but the victim's yield is permanently reduced without consent. This is especially severe when:

1. The owner has announced a fee reduction (attacker front-runs `setFeeInBPS` to force all pending claims at the higher rate before the reduction takes effect).
2. A user deliberately defers claiming to batch claims or minimize fee exposure — the attacker can nullify this strategy at any time.

### Likelihood Explanation
**High.** The function is public and requires no special role. Merkle proofs for all eligible accounts are published off-chain as part of the standard airdrop distribution flow. Any on-chain observer can read `currentMerkleRoot`, `currentIndex`, and `feeInBPS`, then call `claim()` for any unclaimed account using the published proof data. No capital, flash loan, or privileged access is required.

### Recommendation
Add a caller identity check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

This ensures only the entitled account can trigger its own claim, preserving the user's right to choose when to claim and preventing forced fee extraction.

### Proof of Concept

1. Owner deploys `MerkleDistributor` with `feeInBPS = 1000` (10%).
2. Owner calls `setMerkleRoot(root)` — off-chain service publishes all `(index, account, cumulativeAmount, proof)` tuples.
3. Victim (`0xVICTIM`) has `cumulativeAmount = 10_000e18` tokens entitled, has not yet claimed.
4. Attacker reads the published proof for `0xVICTIM`.
5. Attacker calls:
   ```solidity
   merkleDistributor.claim(
       victimIndex,
       0xVICTIM,
       10_000e18,
       victimProof
   );
   ```
6. Contract executes:
   - `claimableAmount = 10_000e18`
   - `fee = 1_000e18` → transferred to `protocolTreasury`
   - `amountToSend = 9_000e18` → transferred to `0xVICTIM`
7. Victim receives 9,000 tokens instead of 10,000. The 1,000-token shortfall is permanent and irrecoverable.
8. If the attacker front-runs a `setFeeInBPS(0)` transaction, every eligible account in the distribution loses up to 10% of their allocation in a single block. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-51)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;

    address public override token;
    address public protocolTreasury;
    uint256 public feeInBPS;
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
