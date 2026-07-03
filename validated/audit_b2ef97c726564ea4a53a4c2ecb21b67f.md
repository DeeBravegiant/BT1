### Title
Retroactive `feeInBPS` Update Applies New Fee to Already-Vested Tokens, Causing Unfair Distribution - (`contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

---

### Summary

`KernelTop100MerkleDistributor` distributes KERNEL tokens over a 30-day linear vesting schedule. The `feeInBPS` parameter is applied at claim time to the vested amount, but can be changed by the owner at any time with no checkpoint. This means a fee change retroactively re-prices the net payout for the entire unclaimed vesting interval, creating unfair treatment between users who claim before vs. after the change — a direct structural analog to the reference report's `rewardRatePerSecond`/`burnBonusBps` issue.

---

### Finding Description

`_getUnclaimedVestedAmount` computes how many tokens have linearly vested since the user's last claim:

```solidity
// contracts/KERNEL/KernelTop100MerkleDistributor.sol
uint256 totalElapsedTime = currentTime - vestingStartTimestamp;
uint256 totalVestedAmount = (userTotalClaimableAmount * totalElapsedTime) / VESTING_DURATION;
uint256 unclaimedAmount = totalVestedAmount - userClaim.amountClaimed;
``` [1](#0-0) 

The fee is then deducted from this vested amount at claim time using the **current** `feeInBPS`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
uint256 amountToSend = claimableAmount - fee;
``` [2](#0-1) 

The owner can change `feeInBPS` at any time with no global checkpoint:

```solidity
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) { revert InvalidFeeInBPS(); }
    feeInBPS = _feeInBPS;
    emit FeeInBPSUpdated(feeInBPS);
}
``` [3](#0-2) 

There is no mechanism to record the fee that was in effect during each sub-interval of the vesting period. The entire unclaimed vested amount is priced at the fee that happens to be current at the moment of the `claim()` call.

The same pattern exists in `KernelMerkleDistributor._processClaim()` and `MerkleDistributor.claim()`, where `feeInBPS` is applied to cumulative merkle-root-encoded allocations at claim time. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

When `feeInBPS` is increased, users who have not yet claimed have a larger fee deducted from tokens that vested under the old (lower) fee regime. The excess fee flows to `protocolTreasury`, not to the user. Tokens that vested while `feeInBPS = 0` can be taxed at up to 10% (`MAX_FEE_IN_BPS = 1000`) if the user delays claiming. Conversely, a fee decrease overpays late claimers relative to early claimers for the same vesting interval. [6](#0-5) 

---

### Likelihood Explanation

**Likelihood: Medium.**

`setFeeInBPS` is a routine admin operation with no timelock or delay. The owner can call it at any time. No malicious intent is required — a legitimate fee adjustment (e.g., reducing the fee to zero as a promotion, or increasing it to fund operations) is sufficient to trigger the disparity. The vesting window is 30 days, giving a wide window during which a fee change can affect unclaimed vested amounts. [3](#0-2) 

---

### Recommendation

Before applying a fee change, checkpoint all outstanding vested-but-unclaimed amounts at the current fee. The simplest approach is to record a `feeInBPS` snapshot alongside each user's `lastClaimTimestamp` and `amountClaimed`, and apply the snapshotted fee to the interval `[lastClaimTimestamp, feeChangeTimestamp]` and the new fee only to `[feeChangeTimestamp, now]`. Alternatively, introduce fee epochs with timestamps and compute piecewise across epochs intersecting the unclaimed interval — mirroring the recommendation in the reference report.

---

### Proof of Concept

**Setup**: `vestingStartTimestamp = T`, `VESTING_DURATION = 30 days`, `feeInBPS = 0`. Both UserA and UserB have 1000 KERNEL allocated in the merkle root.

1. **Day 15 (T + 15 days)**: UserA calls `claim()`.
   - `_getUnclaimedVestedAmount` → `(1000 * 15 days) / 30 days = 500` tokens vested.
   - `fee = 500 * 0 / 10000 = 0`. UserA receives **500 KERNEL**.

2. **Day 15**: Owner calls `setFeeInBPS(1000)` (10% fee).

3. **Day 30 (T + 30 days)**: UserA calls `claim()` again.
   - `_getUnclaimedVestedAmount` → `totalVested = 1000`, `amountClaimed = 500`, `unclaimedAmount = 500`.
   - `fee = 500 * 1000 / 10000 = 50`. UserA receives **450 KERNEL**.
   - **UserA total: 950 KERNEL**.

4. **Day 30**: UserB calls `claim()` for the first time.
   - `_getUnclaimedVestedAmount` → `unclaimedAmount = 1000`.
   - `fee = 1000 * 1000 / 10000 = 100`. UserB receives **900 KERNEL**.
   - **UserB total: 900 KERNEL**.

**Result**: UserA and UserB both vested for the same 30-day period from the same allocation, but UserA receives 5.6% more (950 vs 900). The 50 KERNEL difference is silently redirected to `protocolTreasury` from UserB's allocation that vested entirely before the fee change. [7](#0-6)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L120-127)
```text
    /// @notice The fee denominator constant used to calculate the fee
    uint256 public constant FEE_DENOMINATOR = 10_000;

    /// @notice The maximum fee in basis points that can be set by the owner (10%)
    uint256 public constant MAX_FEE_IN_BPS = 1000;

    /// @notice The vesting duration in seconds (30 days)
    uint256 public constant VESTING_DURATION = 30 days;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L261-270)
```text
        uint256 totalElapsedTime = currentTime - vestingStartTimestamp;
        uint256 totalVestedAmount = (userTotalClaimableAmount * totalElapsedTime) / VESTING_DURATION;

        // Cap at total amount
        if (totalVestedAmount > userTotalClaimableAmount) {
            totalVestedAmount = userTotalClaimableAmount;
        }

        // Calculate unclaimed amount
        uint256 unclaimedAmount = totalVestedAmount - userClaim.amountClaimed;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-337)
```text
    function claim(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
        address user = msg.sender;

        // Verify merkle proof and update user claim data
        _verifyClaimProof(user, amount, merkleProof);

        // Get claimable amount
        uint256 claimableAmount = _getUnclaimedVestedAmount(user, amount);

        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim data
        userClaims[user].lastClaimTimestamp = block.timestamp;
        userClaims[user].amountClaimed += claimableAmount;

        // Calculate fee
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        // Transfer tokens
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
        kernel.safeTransfer(user, amountToSend);

        emit Claimed(user, amountToSend);
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L337-339)
```text
        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-139)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;
```
