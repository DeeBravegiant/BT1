### Title
Fee Rate Changeable Without Timelock During Active 30-Day Vesting, Stealing Claimants' Yield - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

---

### Summary

`KernelTop100MerkleDistributor` distributes KERNEL tokens to users over a fixed 30-day vesting schedule. The `feeInBPS` (up to 10%) is applied at claim time and can be raised by the owner at any moment with no timelock or delay. Because users' tokens are locked inside the vesting schedule and cannot be retrieved without triggering the fee, the owner can silently increase the fee after vesting begins and extract up to 10% of every user's vested KERNEL allocation.

---

### Finding Description

`KernelTop100MerkleDistributor` enforces a hard 30-day linear vesting period (`VESTING_DURATION = 30 days`) for all allocated KERNEL tokens. [1](#0-0) 

The fee is applied at the moment of claiming, not at the moment of allocation:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
uint256 amountToSend = claimableAmount - fee;
``` [2](#0-1) 

The same fee-at-claim pattern applies in `claimAndStake`: [3](#0-2) 

The owner can change `feeInBPS` at any time, up to `MAX_FEE_IN_BPS = 1000` (10%), with no timelock, no delay, and no notice requirement:

```solidity
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) {
        revert InvalidFeeInBPS();
    }
    feeInBPS = _feeInBPS;
    emit FeeInBPSUpdated(feeInBPS);
}
``` [4](#0-3) 

Once vesting has started, users have no mechanism to exit. Their tokens vest linearly and can only be retrieved by calling `claim()` or `claimAndStake()`, both of which apply the current `feeInBPS` at execution time. Users cannot "un-vest" or cancel their allocation. [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

The owner can raise `feeInBPS` from 0 to 1000 (10%) at any point during the 30-day vesting window. Every user who claims after the increase pays the higher fee on their vested KERNEL. Because the vesting schedule is fixed and irrevocable, users cannot avoid the fee: they must either claim and pay it, or delay claiming (but the fee remains in effect for all future claims). Up to 10% of the entire KERNEL distribution can be redirected to `protocolTreasury` without users' consent.

---

### Likelihood Explanation

**Medium.** The attack requires only a single privileged transaction (`setFeeInBPS`) and can be executed at any time during the 30-day vesting window. No external conditions, oracle manipulation, or multi-step setup is needed. The owner role is a single key or multisig; if the key is compromised or the owner acts maliciously, the attack is trivially executable. The 30-day vesting window gives a long exploitation window.

---

### Recommendation

1. **Timelock fee changes**: Require a mandatory delay (e.g., 7 days) between announcing and applying a fee change, giving users time to claim before the new fee takes effect.
2. **Lock fee at vesting start**: Snapshot `feeInBPS` at the time `vestingStartTimestamp` is set and prevent any changes after vesting begins.
3. **Apply fee at allocation time**: Record the fee rate per user at the time their merkle allocation is established, not at claim time.

---

### Proof of Concept

1. Owner deploys `KernelTop100MerkleDistributor` with `feeInBPS = 0` and a merkle root covering 1,000,000 KERNEL across all users.
2. `vestingStartTimestamp` is set; the 30-day vesting window begins.
3. Users observe 0% fee and plan to claim their vested tokens throughout the month.
4. On day 15, owner calls `setFeeInBPS(1000)` — fee is now 10%, effective immediately.
5. Any user who calls `claim()` or `claimAndStake()` from this point forward receives only 90% of their vested KERNEL.
   - `fee = (claimableAmount * 1000) / 10_000` → 10% sent to `protocolTreasury`. [6](#0-5) 
6. Users who have not yet claimed (still in the vesting window) have no recourse: they cannot exit the vesting schedule and must pay the 10% fee on all remaining vested tokens.
7. On a 1,000,000 KERNEL distribution, the owner extracts up to 100,000 KERNEL from users who trusted the original 0% fee.

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L126-127)
```text
    /// @notice The vesting duration in seconds (30 days)
    uint256 public constant VESTING_DURATION = 30 days;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L235-272)
```text
    function _getUnclaimedVestedAmount(address user, uint256 userTotalClaimableAmount) internal view returns (uint256) {
        UserClaim storage userClaim = userClaims[user];

        // If user has claimed everything, return 0
        if (userClaim.amountClaimed >= userTotalClaimableAmount) {
            return 0;
        }

        // Calculate vesting end time
        uint256 vestingEndTime = vestingStartTimestamp + VESTING_DURATION;

        // Calculate start and end times for the period
        uint256 startTime = userClaim.lastClaimTimestamp > 0 ? userClaim.lastClaimTimestamp : vestingStartTimestamp;

        // Cap current time at vesting end time
        uint256 currentTime = block.timestamp;
        if (currentTime > vestingEndTime) {
            currentTime = vestingEndTime;
        }

        // If current time is before start time or vesting hasn't started yet, nothing to claim
        if (currentTime <= startTime || currentTime <= vestingStartTimestamp) {
            return 0;
        }

        // Calculate total vested amount based on time elapsed since vesting start
        uint256 totalElapsedTime = currentTime - vestingStartTimestamp;
        uint256 totalVestedAmount = (userTotalClaimableAmount * totalElapsedTime) / VESTING_DURATION;

        // Cap at total amount
        if (totalVestedAmount > userTotalClaimableAmount) {
            totalVestedAmount = userTotalClaimableAmount;
        }

        // Calculate unclaimed amount
        uint256 unclaimedAmount = totalVestedAmount - userClaim.amountClaimed;

        return unclaimedAmount;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L327-334)
```text
        // Calculate fee
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        // Transfer tokens
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L362-364)
```text
        // Calculate fee
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToStake = claimableAmount - fee;
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
