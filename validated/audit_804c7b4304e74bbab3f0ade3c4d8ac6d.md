### Title
Unguarded `claim()` in `MerkleDistributor` Allows Anyone to Force Yield Claims on Behalf of Any User, Permanently Burning Fee from Victim's Rewards - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` accepts a caller-controlled `account` parameter with no `msg.sender == account` enforcement. Any unprivileged caller can force a claim for any user's address, permanently deducting the protocol fee (up to 10%) from the victim's unclaimed yield and sending it to `protocolTreasury`.

---

### Finding Description

`MerkleDistributor.claim()` takes `account` as an external parameter and performs no check that `msg.sender == account`: [1](#0-0) 

The function deducts a fee of up to 10% (`MAX_FEE_IN_BPS = 1000`) from the claimable amount and routes it to `protocolTreasury`, sending only the remainder to `account`: [2](#0-1) 

Because the merkle proof is tied to `account` (not `msg.sender`), any caller who obtains the victim's publicly-available proof can call `claim(index, victim, cumulativeAmount, victimProof)`. This:

1. Marks the victim's claim as consumed at the current index.
2. Permanently deducts the fee from the victim's yield and sends it to `protocolTreasury`.
3. Prevents the victim from choosing when or how to claim (e.g., waiting for a fee reduction, or using a different path).

The developers recognized this exact pattern and fixed it in the newer `KernelMerkleDistributor._processClaim()`: [3](#0-2) 

The fix was not backported to `MerkleDistributor`.

---

### Impact Explanation

**High. Theft of unclaimed yield.**

When `feeInBPS > 0`, a forced claim permanently reduces the victim's yield by up to 10%. The victim has no recourse — the fee is already transferred to `protocolTreasury` and the claim index is consumed. The victim also loses the ability to time their claim (e.g., after a fee reduction or governance change).

---

### Likelihood Explanation

**High.** The entry path requires no privilege. Merkle proofs for distribution contracts are published off-chain for users to self-claim; any observer can collect a victim's `(index, account, cumulativeAmount, merkleProof)` tuple and replay it. The only precondition is that the victim has not yet claimed.

---

### Recommendation

Add `require(account == msg.sender, "Unauthorized")` at the top of `MerkleDistributor.claim()`, consistent with the guard already present in `KernelMerkleDistributor._processClaim()`: [3](#0-2) 

---

### Proof of Concept

```
Setup:
  MerkleDistributor deployed with feeInBPS = 500 (5%)
  Alice is entitled to 100e18 tokens at index 1
  Alice's merkle proof is publicly available (standard off-chain distribution)

Attack:
  attacker calls:
    MerkleDistributor.claim(1, alice, 100e18, aliceProof)

Result:
  - MerkleDistributor sends 95e18 tokens to alice
  - MerkleDistributor sends 5e18 tokens to protocolTreasury
  - userClaims[alice].lastClaimedIndex = 1
  - userClaims[alice].cumulativeAmount = 100e18

  Alice permanently loses 5e18 tokens (5% of her rewards).
  Alice's claim index is consumed; she cannot re-claim.
  Alice never consented to claiming at this time or fee rate.
``` [4](#0-3)

### Citations

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
