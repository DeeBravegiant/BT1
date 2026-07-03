### Title
Permissionless `claim()` Allows Any Caller to Force a Claim for Any Account at the Current Fee Rate — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

The `claim()` function in `MerkleDistributor` imposes no restriction that `msg.sender == account`. Any third party who possesses (or can derive) a valid Merkle proof for a victim's leaf can trigger the victim's claim at any time, including when `feeInBPS` is at its maximum of 10%. The victim receives their tokens minus the fee and cannot reclaim, permanently losing the fee portion of their yield.

---

### Finding Description

`claim()` accepts an arbitrary `account` parameter and transfers tokens to that address after deducting a fee: [1](#0-0) 

There is no check that `msg.sender == account`. The only guards are:

- Index bounds and `isClaimed` check [2](#0-1) 
- Merkle proof verification against `currentMerkleRoot` only [3](#0-2) 

Once the state is updated and the fee deducted: [4](#0-3) 

…the victim's `lastClaimedIndex` is set to `index` and `cumulativeAmount` is set to `cumulativeAmount`. Any subsequent call by the victim for the same index reverts with `AlreadyClaimed`.

Merkle proofs are public data (the distributor is a Merkle *distributor* — the tree data must be published for users to claim). An attacker can trivially obtain any leaf's proof and call `claim()` for any victim.

---

### Impact Explanation

- `feeInBPS` can be up to `MAX_FEE_IN_BPS = 1000` (10%). [5](#0-4) 
- A victim who intends to wait for a lower fee (e.g., `feeInBPS` drops to 0) is forced to "claim" at the attacker-chosen moment.
- The fee is irrecoverably sent to `protocolTreasury`; the victim cannot reclaim it. [6](#0-5) 
- Impact: **High — Theft of unclaimed yield.** Up to 10% of every user's cumulative entitlement can be forcibly extracted as a fee by any attacker at any time.

---

### Likelihood Explanation

- Requires no special role, no private key, no governance capture.
- Merkle proofs are public by design.
- Attacker only needs to call one public function with publicly available inputs.
- Likelihood: **High.**

---

### Recommendation

Add a caller restriction to `claim()`:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    if (msg.sender != account) revert Unauthorized();
    // ...
}
```

Alternatively, if third-party claiming is intentional (e.g., for gas relayers), introduce an explicit opt-in delegation mechanism so users can authorize specific callers.

---

### Proof of Concept

```solidity
// Setup:
// feeInBPS = 1000 (10%)
// currentIndex = 2
// currentMerkleRoot = R2
// R2 leaf: (index=2, victim, cumulativeAmount=200)
// victim.userClaims = {lastClaimedIndex: 0, cumulativeAmount: 0}

// Attacker obtains victim's proof for R2 from public tree data.
// Attacker calls:
merkleDistributor.claim(2, victim, 200, proofR2);

// Result:
// fee = 200 * 1000 / 10000 = 20 tokens → protocolTreasury
// amountToSend = 180 tokens → victim
// userClaims[victim] = {lastClaimedIndex: 2, cumulativeAmount: 200}

// Victim attempts to claim:
merkleDistributor.claim(2, victim, 200, proofR2);
// Reverts: AlreadyClaimed

// Victim permanently lost 20 tokens (10% of entitlement) they could have
// avoided by waiting for feeInBPS to decrease.
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-103)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L111-117)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L120-123)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L134-144)
```text
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```
