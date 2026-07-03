### Title
Arithmetic Underflow in `claim()` Permanently Freezes User Claims When New Merkle Root Contains Lower `cumulativeAmount` — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim()` performs an unchecked subtraction `cumulativeAmount - userClaims[account].cumulativeAmount` at line 126. Solidity 0.8 reverts on arithmetic underflow. If the owner publishes a new Merkle root whose leaf for a user encodes a `cumulativeAmount` lower than the value already stored in `userClaims[account].cumulativeAmount`, every call to `claim()` for that root will revert. If all subsequent roots also carry a lower value, the user is permanently unable to claim any future distributions.

---

### Finding Description

The `claim` function in `MerkleDistributor` uses a cumulative-amount model: each new root is expected to encode a value ≥ the user's previously recorded cumulative amount, so the delta is always non-negative. [1](#0-0) 

There is **no on-chain guard** enforcing `cumulativeAmount >= userClaims[account].cumulativeAmount` before the subtraction. The contract relies entirely on the off-chain root-generation system to maintain monotonicity.

`setMerkleRoot` simply stores whatever root the owner provides: [2](#0-1) 

Concrete execution path:

1. Root at `index=1` is published; user calls `claim(1, account, 500, proof1)` → succeeds → `userClaims[account].cumulativeAmount = 500`.
2. Off-chain system publishes a corrected root at `index=2` (e.g., due to a rebase, accounting correction, or off-chain bug) with `cumulativeAmount = 400` for the same user.
3. User calls `claim(2, account, 400, proof2)`:
   - `isClaimed(2, account)` → `lastClaimedIndex(1) >= 2` → **false** (passes).
   - Merkle proof verification → **passes** (valid leaf).
   - `claimableAmount = 400 - 500` → **arithmetic underflow → revert** (Solidity 0.8).
4. Because the revert happens before any state write, `lastClaimedIndex` stays at 1 and `cumulativeAmount` stays at 500.
5. The user can never claim index=2. If every subsequent root also encodes an amount < 500, the user is permanently frozen.

The same pattern exists identically in `KernelMerkleDistributor._processClaim()`: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.** A user whose stored `cumulativeAmount` exceeds every future root's leaf value for their address can never successfully call `claim()` again. Their entitled tokens remain locked in the distributor contract indefinitely. This is not a theoretical edge case: rebase tokens (rsETH is a liquid-staking token subject to slashing or rebase events) or off-chain calculation corrections can legitimately produce a lower cumulative figure.

---

### Likelihood Explanation

**Medium.** The trigger requires the owner to publish a root with a lower cumulative value for at least one user. This does not require malice — it can result from:
- A rebase or slashing event that reduces accrued rewards.
- An off-chain accounting correction after an over-allocation.
- A bug in the Merkle tree generation script.

The owner action (`setMerkleRoot`) is routine and expected; the contract provides no safety net.

---

### Recommendation

Add an explicit underflow guard before the subtraction:

```solidity
// In claim() / _processClaim()
if (cumulativeAmount < userClaims[account].cumulativeAmount) {
    revert InvalidCumulativeAmount(); // or simply revert NoTokensToClaim()
}
uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

This converts a hard revert-with-freeze into a clean, recoverable error. The user can then wait for a root that restores a higher cumulative value, or the protocol can handle the correction off-chain without permanently locking users.

---

### Proof of Concept

```solidity
// Foundry test (unmodified contract)
function test_underflow_freezes_user() public {
    // Setup: publish root1 with cumulativeAmount=500 for alice
    bytes32 root1 = buildRoot(1, alice, 500);
    distributor.setMerkleRoot(root1);

    // Alice claims successfully
    distributor.claim(1, alice, 500, proof(1, alice, 500));
    assertEq(distributor.userClaims(alice).cumulativeAmount, 500);

    // Off-chain correction: publish root2 with cumulativeAmount=400
    bytes32 root2 = buildRoot(2, alice, 400);
    distributor.setMerkleRoot(root2);

    // Alice's claim reverts with arithmetic underflow (panic 0x11)
    vm.expectRevert(stdError.arithmeticError);
    distributor.claim(2, alice, 400, proof(2, alice, 400));

    // State unchanged — alice is frozen for any future root with amount < 500
    assertEq(distributor.userClaims(alice).cumulativeAmount, 500);
    assertEq(distributor.userClaims(alice).lastClaimedIndex, 1);
}
``` [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L125-131)
```text
        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L156-167)
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L325-331)
```text
        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }
```
