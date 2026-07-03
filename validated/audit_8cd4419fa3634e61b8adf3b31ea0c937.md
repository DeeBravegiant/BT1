### Title
Arithmetic Underflow in `claim()` Permanently Freezes User Yield When Cumulative Gold Decreases Across Roots — (`contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol`)

---

### Summary

The `claim` function performs unchecked subtractions against stored cumulative values. If the operator publishes a new merkle root where a user's `cumulativeBlastGoldAmount` is lower than what the user already claimed, Solidity 0.8.27's built-in underflow protection causes a panic revert on line 118 — before the `NoPointsToClaim` guard on line 121 is ever evaluated. The user is permanently unable to claim any future entitlement, even if their points balance legitimately increased.

---

### Finding Description

The `claim` function computes claimable deltas by subtracting stored cumulative values from the new root's values: [1](#0-0) 

```solidity
uint256 claimableBlastPoints = cumulativeBlastPointAmount - userClaims[account].cumulativeBlastPointAmount;
uint256 claimableBlastGold   = cumulativeBlastGoldAmount  - userClaims[account].cumulativeBlastGoldAmount;
```

The guard that follows only protects against the case where both deltas are zero: [2](#0-1) 

```solidity
if (claimableBlastPoints == 0 && claimableBlastGold == 0) revert NoPointsToClaim();
```

There is **no on-chain check** that the new root's cumulative values are monotonically non-decreasing relative to what the user already claimed. The `setMerkleRoot` function imposes no such constraint either: [3](#0-2) 

**Concrete scenario:**

| Step | Action | `cumulativeBlastPointAmount` | `cumulativeBlastGoldAmount` |
|------|--------|-----------------------------|-----------------------------|
| Root-N claim | User claims successfully | 10 | 100 |
| Root-N+1 published | Operator corrects gold (off-chain accounting fix) | 50 | 80 |
| Root-N+1 claim attempt | Line 118: `80 - 100` → **underflow panic revert** | — | — |

Because Solidity 0.8.27 reverts on underflow, the transaction fails before line 121. The user's `lastClaimedIndex` remains at N, so they cannot re-claim root-N (`AlreadyClaimed`). They are stuck until a future root restores gold ≥ 100 — which may never happen if the off-chain system treats the correction as canonical. [4](#0-3) 

---

### Impact Explanation

**Actual impact: Medium — Permanent freezing of unclaimed yield.**

The claimed scope of "Critical. Protocol insolvency" is overstated. This contract holds no ERC-20 tokens and performs no token transfers; it only records claims via events and storage. Blast Points/Gold are off-chain yield rewards. The concrete harm is that affected users cannot emit a valid `Claimed` event for their legitimate entitlement, permanently blocking their off-chain yield receipt. This maps to **Medium. Permanent freezing of unclaimed yield**, not protocol insolvency.

---

### Likelihood Explanation

Moderate. The trigger is an operator publishing a corrected root where any user's cumulative gold decreases — a realistic scenario during off-chain accounting corrections or merkle tree generation bugs. No malicious intent is required; an honest mistake suffices. The contract provides no recovery path once a user is frozen.

---

### Recommendation

Add monotonicity guards before the subtractions:

```solidity
if (cumulativeBlastPointAmount < userClaims[account].cumulativeBlastPointAmount ||
    cumulativeBlastGoldAmount   < userClaims[account].cumulativeBlastGoldAmount) {
    revert InvalidCumulativeAmount();
}
```

This converts a silent permanent freeze into an explicit, recoverable revert at the root-publishing layer, and makes the invariant enforceable on-chain.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fuzz: pointsN=10, goldN=100, pointsN1=50, goldN1=80
// Constraint: pointsN1 > pointsN && goldN1 < goldN

function test_underflow_freezes_user(
    uint256 pointsN,  uint256 goldN,
    uint256 pointsN1, uint256 goldN1
) external {
    vm.assume(pointsN1 > pointsN);
    vm.assume(goldN1   < goldN);   // gold "corrected" downward

    // Step 1: claim at root-N
    bytes32 rootN = buildRoot(index1, user, pointsN, goldN);
    distributor.setMerkleRoot(rootN);
    distributor.claim(1, user, pointsN, goldN, proofN);
    // userClaims[user] = {lastClaimedIndex:1, points:pointsN, gold:goldN}

    // Step 2: publish root-N+1 with decreased gold
    bytes32 rootN1 = buildRoot(index2, user, pointsN1, goldN1);
    distributor.setMerkleRoot(rootN1);

    // Step 3: claim at root-N+1 — ALWAYS REVERTS with arithmetic underflow
    // Line 118: goldN1 - goldN underflows because goldN1 < goldN
    vm.expectRevert(stdError.arithmeticError);
    distributor.claim(2, user, pointsN1, goldN1, proofN1);
    // User is now frozen: cannot claim root-N (AlreadyClaimed), cannot claim root-N+1 (underflow)
}
``` [5](#0-4)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L105-107)
```text
        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L116-123)
```text
        // Calculate the claimable amount
        uint256 claimableBlastPoints = cumulativeBlastPointAmount - userClaims[account].cumulativeBlastPointAmount;
        uint256 claimableBlastGold = cumulativeBlastGoldAmount - userClaims[account].cumulativeBlastGoldAmount;

        // Ensure there is something to claim
        if (claimableBlastPoints == 0 && claimableBlastGold == 0) {
            revert NoPointsToClaim();
        }
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
