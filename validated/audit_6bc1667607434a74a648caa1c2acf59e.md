### Title
Unrestricted caller in `MerkleDistributor.claim()` enables forced fee extraction from any user — (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never verifies that `msg.sender == account`. Any caller who possesses a valid Merkle proof for a victim can trigger that victim's claim, forcing them to pay the current protocol fee (`feeInBPS`, up to 10%) at a time of the attacker's choosing. This is the direct structural analog of the session-signature replay: just as the session preimage omitted the wallet address and allowed the same signature to execute on any sibling wallet, the claim preimage omits caller binding and allows any party to submit a valid proof on behalf of any account.

---

### Finding Description

`MerkleDistributor.claim()` (lines 97–147) accepts `account` as a caller-supplied parameter, verifies the Merkle proof against `keccak256(abi.encodePacked(index, account, cumulativeAmount))`, and transfers tokens to `account` after deducting a fee. There is no check that `msg.sender == account`. [1](#0-0) 

The fee deduction path: [2](#0-1) 

The maximum fee is 10%: [3](#0-2) 

By contrast, `KernelMerkleDistributor._processClaim()` explicitly enforces caller binding: [4](#0-3) 

**Attack path:**

1. The protocol publishes Merkle proofs off-chain for users to retrieve (standard practice).
2. An attacker observes victim's valid tuple `(index, account, cumulativeAmount, proof)`.
3. Attacker calls `MerkleDistributor.claim(index, victimAccount, cumulativeAmount, proof)` directly.
4. The contract verifies the proof (it is valid), deducts `fee = claimableAmount * feeInBPS / 10_000`, sends `claimableAmount - fee` to `victimAccount`, and sends `fee` to `protocolTreasury`.
5. `userClaims[account].cumulativeAmount` is updated to `cumulativeAmount`, permanently consuming the victim's entitlement at the attacker-chosen fee rate. [5](#0-4) 

The victim cannot reclaim the fee portion — the state update is final. If the victim was waiting for `feeInBPS` to be reduced (e.g., from 10% to 0%), the attacker permanently denies them that option.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

The Merkle tree encodes each user's full entitlement as `cumulativeAmount`. The fee is deducted at claim time and is configurable up to 10% (`MAX_FEE_IN_BPS = 1000`). An attacker who forces a claim at peak fee rate causes the victim to permanently lose up to 10% of their token entitlement. Because `userClaims[account].cumulativeAmount` is set to `cumulativeAmount` after the forced claim, the victim has no recourse — the lost fee cannot be recovered through any subsequent claim.

---

### Likelihood Explanation

**High.** Merkle proofs for distribution contracts are routinely published in public repositories, IPFS, or protocol frontends so users can self-serve. Any unprivileged attacker can read these proofs and call `claim()` for any account. The only precondition is `feeInBPS > 0`, which is a live configurable parameter. The attack requires no special access, no flash loan, and no coordination — a single transaction suffices.

---

### Recommendation

Add a caller binding check in `MerkleDistributor.claim()`, mirroring the guard already present in `KernelMerkleDistributor._processClaim()`:

```solidity
// In MerkleDistributor.claim(), before proof verification:
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

---

### Proof of Concept

```
Setup:
  - MerkleDistributor deployed with feeInBPS = 500 (5%)
  - Merkle root set; victim's leaf: (index=1, account=victim, cumulativeAmount=1000e18)
  - Victim is waiting for feeInBPS to be set to 0 before claiming

Attack:
  attacker.call(
      merkleDistributor.claim(1, victim, 1000e18, validProof)
  )

Result:
  - victim receives 950e18 tokens  (1000e18 - 50e18 fee)
  - protocolTreasury receives 50e18 tokens
  - userClaims[victim].cumulativeAmount = 1000e18  (fully consumed)
  - victim permanently loses 50e18 tokens they would have received
    had they claimed after feeInBPS was reduced to 0
``` [6](#0-5)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-146)
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
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
