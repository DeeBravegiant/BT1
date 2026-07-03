I have the full contract. Let me verify the exact lines for the critical checks.

### Title
Unprivileged Pre-Claim Enables Arithmetic Underflow Freezing Victim's Blast Points — (`contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol`)

---

### Summary

`MerkleBlastPointsDistributor.claim()` has no `msg.sender == account` guard, allowing any caller to submit a valid merkle claim on behalf of any victim. Combined with the unchecked cumulative subtraction in Solidity 0.8.27, an attacker can advance a victim's stored cumulative to value `X`, after which any subsequent root that encodes a cumulative `< X` for that victim causes an arithmetic underflow revert, temporarily freezing the victim's ability to record further Blast Point claims.

---

### Finding Description

**Root cause — no caller restriction on `claim()`:**

The `claim()` function accepts an arbitrary `account` parameter and performs no check that `msg.sender == account`: [1](#0-0) 

Any address can call `claim(index, victim, X, Y, proof)` with a valid proof for `victim`, and the contract will update `victim`'s stored state unconditionally.

**Underflow site — unchecked cumulative subtraction:**

After the proof passes, the claimable delta is computed as: [2](#0-1) 

In Solidity 0.8.27 these are checked arithmetic operations. If `userClaims[account].cumulativeBlastPointAmount` (set by the attacker's pre-claim) exceeds the `cumulativeBlastPointAmount` encoded in the next root, the subtraction reverts with an arithmetic underflow panic.

**State written by the attacker's pre-claim:** [3](#0-2) 

Once the attacker's call succeeds, `userClaims[victim].cumulativeBlastPointAmount = X` is persisted on-chain. The victim cannot undo this.

**`setMerkleRoot` — no monotonicity enforcement:** [4](#0-3) 

The contract places no constraint that the new root must encode cumulative values `≥` those in the previous root. A corrected root (fixing an off-chain overcount) can legitimately encode a lower cumulative for a user.

---

### Impact Explanation

**Scoped impact: Medium — Temporary freezing of funds.**

The victim cannot call `claim()` for any root that encodes a cumulative `< X` (the value the attacker wrote). Their Blast Points allocation is inaccessible on-chain until the owner publishes a new root whose cumulative for the victim is `≥ X`. During that window the victim's unclaimed Blast Points are frozen.

---

### Likelihood Explanation

**Low-Medium.** Two conditions must coincide:

1. The owner publishes a root at index `N` with victim cumulative `X`, then later publishes a corrected root at `N+1` with cumulative `X-1` (a realistic off-chain recomputation / overcount correction scenario).
2. An attacker observes root `N` and calls `claim()` for the victim before the victim does, advancing the victim's stored cumulative to `X`.

Neither condition requires key compromise or governance capture. Off-chain recomputation errors in Blast Points accounting are realistic, and the lack of a caller guard makes step 2 trivially executable by any EOA with the public merkle proof.

---

### Recommendation

1. **Add a caller restriction**: require `msg.sender == account` in `claim()`, so only the beneficiary can advance their own stored cumulative.
2. **Enforce monotonicity on-chain**: when setting a new root, optionally require that per-user cumulatives in the new tree are `≥` those in the previous tree (enforced off-chain at root publication time, documented as an invariant).
3. **Alternatively**, store the `lastClaimedIndex` and allow claims against any historical root index that is `> lastClaimedIndex`, so a corrected root does not retroactively break prior state.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Pseudocode — run on a local fork or Foundry test

function testUnderflowFreeze() public {
    // 1. Owner publishes root N encoding victim cumulative = 100 pts
    distributor.setMerkleRoot(rootN);  // currentIndex = N

    // 2. Attacker (not victim) submits victim's valid claim at index N
    bytes32[] memory proof = buildProof(N, victim, 100, 0);
    vm.prank(attacker);
    distributor.claim(N, victim, 100, 0, proof);
    // userClaims[victim].cumulativeBlastPointAmount == 100

    // 3. Owner publishes corrected root N+1 encoding victim cumulative = 99
    distributor.setMerkleRoot(rootN1);  // currentIndex = N+1

    // 4. Victim tries to claim at N+1
    bytes32[] memory proof2 = buildProof(N+1, victim, 99, 0);
    vm.prank(victim);
    vm.expectRevert(stdError.arithmeticError);  // 99 - 100 underflows
    distributor.claim(N+1, victim, 99, 0, proof2);
    // Victim's Blast Points are frozen until a new root with cumulative >= 100 is published
}
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L86-95)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeBlastPointAmount,
        uint256 cumulativeBlastGoldAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        whenNotPaused
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L117-118)
```text
        uint256 claimableBlastPoints = cumulativeBlastPointAmount - userClaims[account].cumulativeBlastPointAmount;
        uint256 claimableBlastGold = cumulativeBlastGoldAmount - userClaims[account].cumulativeBlastGoldAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L126-128)
```text
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeBlastPointAmount = cumulativeBlastPointAmount;
        userClaims[account].cumulativeBlastGoldAmount = cumulativeBlastGoldAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L140-151)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```
