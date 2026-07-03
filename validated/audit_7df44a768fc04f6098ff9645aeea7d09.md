### Title
Any caller can force-claim on behalf of any user, extracting protocol fees from their allocation - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter with no check that `msg.sender == account`. Any unprivileged caller who possesses a valid merkle proof for a victim can trigger the victim's claim, causing the protocol fee (`feeInBPS`) to be deducted from the victim's allocation and sent to `protocolTreasury` — yield the victim never consented to surrender at that moment.

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
``` [1](#0-0) 

There is no `require(msg.sender == account)` guard anywhere in the function or its callees. After verifying the merkle proof, the function deducts a fee and transfers the remainder to `account`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
``` [2](#0-1) 

The merkle leaf is constructed as `keccak256(abi.encodePacked(index, account, cumulativeAmount))`, so the proof is tied to the victim's address and amount — both of which are public off-chain data published by the protocol. [3](#0-2) 

The sibling contract `KernelMerkleDistributor` explicitly guards against this exact pattern with `if (account != msg.sender) { revert Unauthorized(); }` inside `_processClaim`, confirming the protocol developers recognise the requirement but omitted it from `MerkleDistributor`. [4](#0-3) 

### Impact Explanation

**High — Theft of unclaimed yield.**

A victim's allocation is reduced by up to `MAX_FEE_IN_BPS = 1000` (10%) relative to what they would have received had they chosen their own claim timing. The fee is irrevocably transferred to `protocolTreasury`; the victim cannot recover it. The attacker can repeat this for every eligible address in the merkle tree, draining yield from all participants simultaneously. The victim receives `amountToSend` rather than `claimableAmount`, a direct, permanent loss of entitled tokens. [5](#0-4) 

### Likelihood Explanation

**High.** Merkle proofs for all eligible accounts are published off-chain by the protocol (standard practice for merkle distributors, as documented in the contract's own README). Any external caller can read the proof data, construct a valid call, and submit it in a single transaction with no special privileges, capital, or prior interaction with the protocol. [6](#0-5) 

### Recommendation

Add a caller-identity check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```diff
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
+    if (account != msg.sender) revert Unauthorized();
     ...
 }
``` [7](#0-6) 

### Proof of Concept

1. Protocol publishes merkle root containing `(index=1, victim=0xVICTIM, cumulativeAmount=1000e18)` and the corresponding proof.
2. Attacker calls:
   ```solidity
   merkleDistributor.claim(1, 0xVICTIM, 1000e18, victimProof);
   ```
3. Contract verifies the proof (valid), deducts `fee = 1000e18 * feeInBPS / 10_000`, transfers `amountToSend` to `0xVICTIM` and `fee` to `protocolTreasury`.
4. `userClaims[0xVICTIM].lastClaimedIndex` is now set to `1`; the victim's claim for this index is permanently consumed.
5. Victim calls `claim()` themselves — reverts with `AlreadyClaimed`.
6. Victim has permanently lost `fee` tokens they were entitled to receive in full. [8](#0-7)

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

**File:** contracts/utils/MerkleDistributor/README.md (L24-30)
```markdown
To claim tokens, a user needs to provide:
- The `index` corresponding to their position in the Merkle tree.
- Their `account` address where the tokens will be sent.
- The `cumulativeAmount` representing the total amount they are entitled to claim up to the current period.
- A valid `merkleProof` proving their entitlement.

The contract calculates the actual claimable amount by subtracting any previously claimed amount from the `cumulativeAmount`. If the claim is valid and there are tokens to claim, the specified amount is transferred to the user's account.
```
