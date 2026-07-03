### Title
Mutable `setFeeInBPS()` Allows Owner to Reduce Claimable KERNEL Rewards After Merkle Root Is Committed - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

---

### Summary

`KernelMerkleDistributor.setFeeInBPS()` allows the owner to change the claim fee at any time, including after the merkle root has been published and users have accrued claimable KERNEL balances. Because the fee is applied at claim time against the cumulative amount encoded in the merkle tree, raising it retroactively reduces what users actually receive relative to what the merkle root entitles them to — with no timelock, no freeze guard, and no ability for users to exit before the change takes effect.

---

### Finding Description

`KernelMerkleDistributor` distributes KERNEL rewards via a cumulative merkle tree. The owner periodically calls `setMerkleRoot()` to publish a new root encoding each user's total earned amount. When a user calls `claim()`, the contract computes the incremental claimable amount and deducts a fee before transferring:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
uint256 amountToSend = claimableAmount - fee;
``` [1](#0-0) 

The fee rate `feeInBPS` is set by the owner via `setFeeInBPS()` with no guard preventing changes after a merkle root has been published:

```solidity
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) {
        revert InvalidFeeInBPS();
    }
    feeInBPS = _feeInBPS;
    emit FeeInBPSUpdated(feeInBPS);
}
``` [2](#0-1) 

The same pattern exists in `KernelTop100MerkleDistributor`: [3](#0-2) 

And in the base `MerkleDistributor`: [4](#0-3) 

Once a merkle root is set, users' entitlements are fixed on-chain. However, users cannot "un-earn" or pre-emptively withdraw their rewards — they must call `claim()` to receive them. Between the moment the root is published and the moment a user claims, the owner can silently raise `feeInBPS` to the maximum allowed value, causing every pending claimant to receive materially less than the merkle tree entitles them to. There is no timelock, no snapshot of the fee at root-publication time, and no mechanism for users to react before the change takes effect.

---

### Impact Explanation

**Theft of unclaimed yield (High).** The merkle root encodes the full cumulative amount each user has earned. Raising `feeInBPS` after root publication diverts a portion of every user's entitled KERNEL to `protocolTreasury` instead. At the maximum allowed fee, the entire claimable increment is redirected. This is a direct, quantifiable reduction in what reward claimants receive — matching the "theft of unclaimed yield" impact category.

---

### Likelihood Explanation

**Medium.** The entry path is straightforward: the owner calls `setFeeInBPS()` after `setMerkleRoot()`. No external dependency, no oracle, no front-running required. The only prerequisite is the owner key acting against users' interests, which is the same trust assumption the original report identified as insufficient. The lack of any on-chain protective mechanism (timelock, fee snapshot at root publication, or freeze-after-first-claim) means there is no technical barrier to exploitation.

---

### Recommendation

Snapshot the fee at the time the merkle root is published, so each root index carries its own immutable fee rate:

```solidity
mapping(uint256 rootIndex => uint256 feeBps) public feeByRootIndex;

function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    // ...existing checks...
    currentMerkleRoot = _merkleRootToSet;
    currentMerkleRootIndex++;
    currentIndex++;
    feeByRootIndex[currentMerkleRootIndex] = feeInBPS; // snapshot
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
```

Alternatively, require a timelock (e.g., 48 hours) before a new `feeInBPS` takes effect, giving users time to claim under the previously advertised fee.

---

### Proof of Concept

1. Owner calls `setMerkleRoot(root)` — root encodes Alice's cumulative entitlement of 1,000 KERNEL.
2. Alice's `lastClaimedIndex = 0`, so her `claimableAmount = 1,000 KERNEL` at the current `feeInBPS = 5%` (expected net: 950 KERNEL).
3. Before Alice submits her claim transaction, owner calls `setFeeInBPS(MAX_FEE_IN_BPS)`.
4. Alice's `claim()` executes: `fee = 1000 * MAX_FEE_IN_BPS / 10_000` — Alice receives far less than 950 KERNEL; the difference is transferred to `protocolTreasury`.
5. Alice had no on-chain mechanism to detect or react to the fee change before her claim was processed. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L331-346)
```text
        }

        // Update user claim info
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L388-396)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        feeInBPS = _feeInBPS;

        emit FeeInBPSUpdated(feeInBPS);
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L426-432)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }
        feeInBPS = _feeInBPS;
        emit FeeInBPSUpdated(feeInBPS);
    }
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
