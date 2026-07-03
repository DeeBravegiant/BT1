### Title
Fee Applied at Claim Time Allows Owner Fee Increase to Reduce Net Payout for Late Claimers vs. Early Claimers — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`KernelMerkleDistributor._processClaim` computes the protocol fee using the **current** `feeInBPS` at the moment of each claim. Because `setFeeInBPS` can be called by the owner at any time with no snapshot or lock tied to a merkle root, two users with identical merkle allocations can receive materially different net KERNEL payouts depending solely on claim timing relative to a fee update.

---

### Finding Description

In `_processClaim`, the fee deduction is:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
uint256 amountToSend = claimableAmount - fee;
``` [1](#0-0) 

`feeInBPS` is a mutable storage variable with no per-root or per-epoch snapshot. The owner can update it at any time via:

```solidity
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) { revert InvalidFeeInBPS(); }
    feeInBPS = _feeInBPS;
    ...
}
``` [2](#0-1) 

The maximum fee is capped at 10% (`MAX_FEE_IN_BPS = 1000`): [3](#0-2) 

There is no mechanism that freezes `feeInBPS` for a given merkle root once it is published. A fee increase between two users' claim transactions — even an entirely routine one — silently reduces the net payout for every user who has not yet claimed.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

Users are allocated a fixed `cumulativeAmount` in the merkle tree. The implicit promise is that all users with the same allocation receive the same net payout (modulo the fee in effect at distribution time). A mid-distribution fee increase breaks this invariant: early claimers keep more KERNEL, late claimers lose up to 10% of their allocation to the treasury compared to early claimers. No funds are permanently lost from the system — they are redirected to `protocolTreasury` — but individual users receive fewer tokens than their allocation implied.

---

### Likelihood Explanation

The owner does not need to act maliciously. A routine fee adjustment (e.g., changing the fee for a new distribution epoch) applied while a prior merkle root still has unclaimed allocations is sufficient to trigger the disparity. The `setFeeInBPS` function has no guard preventing it from being called while claims are open. [2](#0-1) 

---

### Recommendation

Snapshot `feeInBPS` at the time `setMerkleRoot` is called and store it alongside the merkle root. In `_processClaim`, use the fee that was in effect when the root was published rather than the current global `feeInBPS`. Alternatively, emit a clear warning in `setFeeInBPS` documentation that it affects all currently open (unclaimed) distributions, and consider pausing claims before any fee change.

---

### Proof of Concept

```solidity
// Deploy distributor with feeInBPS = 0
// Set merkle root with userA and userB each allocated 1000e18 KERNEL

// Step 1: userA claims → receives 1000e18 (fee = 0)
distributor.claim(index, userA, 1000e18, proofA);
assert(kernel.balanceOf(userA) == 1000e18);

// Step 2: owner raises fee to 10%
distributor.setFeeInBPS(1000); // MAX_FEE_IN_BPS

// Step 3: userB claims → receives only 900e18 (fee = 100e18 sent to treasury)
distributor.claim(index, userB, 1000e18, proofB);
assert(kernel.balanceOf(userB) == 900e18);

// userA received 100e18 more than userB for identical allocations
assert(kernel.balanceOf(userA) > kernel.balanceOf(userB));
```

The fee computation at line 338 uses the live `feeInBPS`, so the outcome differs purely based on claim ordering relative to the owner's `setFeeInBPS` call. [1](#0-0)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L146-146)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L338-339)
```text
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;
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
