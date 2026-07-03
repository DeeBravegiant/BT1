### Title
Missing `msg.sender` Validation in `claim()` Enables Forced Fee-Bearing Claims on Behalf of Any User - (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never verifies that `msg.sender == account`. Any external caller can submit a valid merkle proof for a victim and force a claim on their behalf, causing the victim to pay the current `feeInBPS` (up to 10%) even if they intended to wait for a zero-fee or lower-fee window.

---

### Finding Description

The `claim()` function in `MerkleDistributor` is publicly callable and accepts `account` as a caller-supplied parameter:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
```

The function verifies the merkle proof against `(index, account, cumulativeAmount)` and then transfers tokens to `account` minus a fee:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
...
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;
IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
```

There is no check that `msg.sender == account`. Merkle proofs for all accounts are typically derived from publicly published tree data, so any attacker can reconstruct a valid proof for any victim and call `claim()` on their behalf.

The sibling contract `KernelMerkleDistributor` correctly guards against this in `_processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

`MerkleDistributor` has no equivalent guard.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

`feeInBPS` can be set up to `MAX_FEE_IN_BPS = 1000` (10%). A user who holds off claiming while the fee is high (waiting for the owner to lower it to 0) can be front-run by an attacker who forces the claim at the high-fee moment. The fee is permanently deducted from the user's claimable amount and sent to `protocolTreasury`. The user cannot reclaim it. For a user entitled to 10,000 tokens, this represents a forced loss of up to 1,000 tokens.

---

### Likelihood Explanation

**High.** Merkle tree data (indices, accounts, cumulative amounts, proofs) is routinely published off-chain or derivable from on-chain events. No special privilege is required — any EOA can call `claim()`. The attacker is economically incentivized whenever `feeInBPS > 0`, and the owner can raise the fee at any time before the forced claim is executed.

---

### Recommendation

Add a caller check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

This ensures only the rightful beneficiary can trigger their own claim and choose the timing (and thus the fee rate) that applies to them.

---

### Proof of Concept

1. Owner sets `feeInBPS = 1000` (10%).
2. Alice is entitled to 10,000 tokens; her merkle proof is publicly derivable.
3. Alice decides to wait, expecting the owner to lower the fee.
4. Bob (attacker) calls `MerkleDistributor.claim(index, alice, 10_000e18, aliceProof)`.
5. The call succeeds: Alice receives 9,000 tokens; 1,000 tokens are sent to `protocolTreasury` as fee.
6. Alice's `userClaims[alice].cumulativeAmount` is updated; she cannot reclaim the 1,000 tokens lost to fees.

**Root cause:** [1](#0-0)  — `claim()` accepts `account` without any `msg.sender == account` guard.

**Fee deduction path:** [2](#0-1)  — fee is irreversibly deducted and sent to treasury.

**Correct pattern (missing here):** [3](#0-2)  — `KernelMerkleDistributor` enforces `account == msg.sender`.

**Fee cap:** [4](#0-3)  — `MAX_FEE_IN_BPS = 1000` (10%), confirming the maximum extractable yield per forced claim.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-106)
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
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
