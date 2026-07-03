### Title
Missing Caller Authentication in `MerkleDistributor.claim()` Allows Forced Claims at Unfavorable Fee Rates — (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter without verifying that `msg.sender == account`. Any unprivileged caller can trigger a claim on behalf of any victim. Because a fee (`feeInBPS`, up to 10%) is deducted at claim time and the fee rate is owner-adjustable, an attacker can front-run a fee reduction by force-claiming for all users at the current high fee rate, permanently diverting the fee-difference from users to the treasury. The sister contract `KernelMerkleDistributor` explicitly guards against this with `if (account != msg.sender) revert Unauthorized()`, confirming the check is intentional there and absent here.

---

### Finding Description

`MerkleDistributor.claim()` takes `account` as a caller-supplied parameter and performs no identity check: [1](#0-0) 

The merkle proof binds `(index, account, cumulativeAmount)` together, so the proof is valid only for the correct `account`. However, **anyone** can supply a valid proof for a victim and execute the claim on their behalf: [2](#0-1) 

After the claim, the victim's `lastClaimedIndex` and `cumulativeAmount` are updated, and the fee is deducted and sent to `protocolTreasury`: [3](#0-2) 

The fee rate is mutable via `setFeeInBPS`: [4](#0-3) 

By contrast, `KernelMerkleDistributor._processClaim()` enforces the identity check that is missing here: [5](#0-4) 

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

Attack scenario:

1. `feeInBPS` is currently 1000 (10%). The owner broadcasts a `setFeeInBPS(0)` transaction to eliminate the fee.
2. An attacker observes the pending transaction in the mempool and front-runs it by calling `claim(index, victim, cumulativeAmount, proof)` for every eligible user while the fee is still 10%.
3. Each victim's claim is processed at 10% fee. The fee is irrevocably sent to `protocolTreasury`.
4. The owner's `setFeeInBPS(0)` lands, but all claims have already been settled at the old rate.
5. Every victim permanently loses 10% of their allocation — yield they would have kept had they claimed after the fee reduction.

The tokens go to the correct `account`, so there is no direct theft by the attacker, but the victim's yield is permanently diverted to the treasury without the victim's consent. This satisfies the "theft of unclaimed yield" impact category.

---

### Likelihood Explanation

**Likelihood: Medium.**

- The fee is non-zero by design (up to 10% max, `MAX_FEE_IN_BPS = 1000`).
- Fee reductions are a normal protocol lifecycle event (e.g., promotional periods, governance decisions).
- Mempool front-running of a `setFeeInBPS` call requires no privileged access — any EOA can execute it.
- The attacker needs only the merkle proof data, which is public off-chain data used by the frontend.
- The attack is fully permissionless and requires no capital.

---

### Recommendation

Add an identity check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
+   if (account != msg.sender) revert Unauthorized();
    // ... rest of function
}
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, as `KernelTop100MerkleDistributor.claim()` does: [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - MerkleDistributor deployed with feeInBPS = 1000 (10%)
  - Alice is entitled to 1000 tokens (leaf: keccak256(abi.encodePacked(1, alice, 1000)))
  - Owner broadcasts setFeeInBPS(0) to mempool

Attack (front-runs setFeeInBPS(0)):
  attacker.call(
    merkleDistributor.claim(1, alice, 1000, aliceProof)
  )

Result:
  - Alice receives 900 tokens (10% fee = 100 tokens sent to treasury)
  - setFeeInBPS(0) lands — but Alice's claim is already settled
  - Alice permanently lost 100 tokens she would have kept at 0% fee
  - Attacker spent only gas; no capital required
```

The `isClaimed` check prevents Alice from re-claiming at the 0% rate: [7](#0-6)

### Citations

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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L115-117)
```text
        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L119-146)
```text
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L198-206)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        feeInBPS = _feeInBPS;

        emit FeeInBPSUpdated(_feeInBPS);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-311)
```text
    function claim(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
        address user = msg.sender;
```
