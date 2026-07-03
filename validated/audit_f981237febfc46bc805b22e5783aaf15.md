### Title
Missing Caller Authorization in `MerkleDistributor.claim()` Allows Anyone to Force Claims for Other Users - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never validates that `msg.sender == account`. Any unprivileged caller can trigger a token claim on behalf of any other user at any time, extracting a protocol fee from the victim's claimable balance in the process.

---

### Finding Description

`MerkleDistributor.claim()` takes `account` as a caller-supplied parameter, verifies the merkle proof against it, and transfers tokens to it — but performs no check that the caller is the account owner:

```solidity
function claim(
    uint256 index,
    address account,       // <-- caller-supplied, never validated against msg.sender
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    // ... merkle proof verified against `account` ...
    IERC20(token).safeTransfer(account, amountToSend);
    IERC20(token).safeTransfer(protocolTreasury, fee);   // fee deducted from victim
}
```

The sibling contract `KernelMerkleDistributor` explicitly fixes this exact pattern in its `_processClaim()` internal function:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

`MerkleDistributor` has no equivalent guard. Merkle proof data (index, account, cumulativeAmount) is public — it is posted off-chain and verifiable on-chain — so any attacker can reconstruct valid call arguments for any eligible account.

---

### Impact Explanation

When `feeInBPS > 0` (up to `MAX_FEE_IN_BPS = 1000`, i.e. 10%), the forced claim deducts `claimableAmount * feeInBPS / 10_000` from the victim's balance and routes it to `protocolTreasury`. A user who was deliberately deferring their claim — waiting for the owner to lower or zero the fee — is forced to pay the current (higher) fee. The difference between the fee paid under the forced claim and the fee the user would have paid is an irreversible loss of unclaimed yield. The attacker pays only gas; the protocol treasury receives the extracted fee.

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

Merkle proof inputs are public. Any attacker can enumerate all eligible accounts from the off-chain distribution data and call `claim()` for each one. No special role, private key, or privileged access is required. The only cost is gas. Likelihood is **High**.

---

### Recommendation

Add a caller-identity check identical to the one already present in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Insert this check at the top of `MerkleDistributor.claim()`, before any state changes or transfers.

---

### Proof of Concept

1. Alice is eligible to claim 1000 tokens. `feeInBPS = 500` (5%). Alice is waiting for the owner to reduce the fee to 0.
2. Bob (attacker) reads Alice's merkle proof from the off-chain distribution data.
3. Bob calls `MerkleDistributor.claim(index, alice, 1000e18, proof)`.
4. The function passes all checks (proof is valid, not yet claimed).
5. `fee = 1000e18 * 500 / 10_000 = 50e18` tokens are sent to `protocolTreasury`.
6. `950e18` tokens are sent to Alice.
7. Alice's claim state is marked as claimed; she can never reclaim the 50 tokens lost to the fee.
8. Bob spent only gas. Alice lost 50 tokens she would have avoided losing had she been able to claim after the fee was reduced.

**Root cause line**: [1](#0-0)  — no `msg.sender == account` guard present.

**Fee deduction that harms victim**: [2](#0-1) 

**Correct fix already applied in sibling contract**: [3](#0-2) 

**Fee cap (up to 10%)**: [4](#0-3)

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
