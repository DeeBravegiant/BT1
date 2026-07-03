### Title
`withdrawTokens` Administrative Function Ignores Vested-But-Unclaimed User Entitlements - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

---

### Summary

`KernelTop100MerkleDistributor.sol` distributes KERNEL tokens to merkle-eligible users over a 30-day linear vesting schedule. The owner-only `withdrawTokens` function allows arbitrary withdrawal of any token — including the KERNEL distribution token — with no check against the total vested-but-unclaimed balance owed to users. If called, it can permanently deprive users of KERNEL tokens they have already earned under the vesting schedule.

---

### Finding Description

`KernelTop100MerkleDistributor` holds KERNEL tokens and releases them to users linearly over `VESTING_DURATION` (30 days) from `vestingStartTimestamp`. Each user's entitlement is fixed by a merkle root at deployment. The vesting math in `_getUnclaimedVestedAmount` computes how much of a user's total allocation has vested based on elapsed time, minus what they have already claimed.

The admin function `withdrawTokens` at line 461:

```solidity
function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
    UtilLib.checkNonZeroAddress(_token);
    UtilLib.checkNonZeroAddress(_recipient);
    if (_amount == 0) {
        revert ZeroValueProvided();
    }
    IERC20(_token).safeTransfer(_recipient, _amount);
    emit TokensWithdrawn(_token, _amount, _recipient);
}
```

There is **no check** against:
- The total KERNEL balance currently vested and owed to all eligible users
- The remaining unvested KERNEL that users will be entitled to as time progresses
- Any per-user `amountClaimed` accounting

The owner can call `withdrawTokens(address(kernel), kernel.balanceOf(address(this)), recipient)` and drain the entire KERNEL balance, including all tokens that have already vested for users who have not yet called `claim()`. After such a call, every subsequent `claim()` or `claimAndStake()` call will revert due to insufficient balance, permanently freezing all unclaimed vested yield.

This is the direct analog of `renounceVesting` in the external report: an administrative function that transfers the full token balance of a distribution/vesting contract without first settling what is owed to beneficiaries.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

All KERNEL tokens that have vested (proportional to elapsed time since `vestingStartTimestamp`) but not yet been claimed by users are permanently lost to those users. Since the vesting period is only 30 days and users may not claim daily, a significant fraction of the total distribution can be outstanding at any given time. For example, if 15 days have elapsed and no user has claimed, 50% of the entire KERNEL distribution is vested and owed but can be drained in a single owner transaction.

---

### Likelihood Explanation

The owner must call `withdrawTokens` with `_token = address(kernel)`. This can occur:
1. Intentionally by a malicious or compromised owner.
2. Accidentally by an owner who believes they are recovering "excess" or "mistakenly sent" tokens, not realizing the KERNEL balance is entirely composed of user entitlements.

The function has no guard, no time-lock, and no cap relative to outstanding obligations. The risk is structurally identical to the acknowledged `renounceVesting` issue in the external report.

---

### Recommendation

Add a guard in `withdrawTokens` that prevents withdrawal of the KERNEL distribution token entirely, or at minimum enforces that the remaining balance after withdrawal is sufficient to cover all outstanding vested entitlements. A simpler mitigation is to disallow `_token == address(kernel)` in `withdrawTokens`, since the KERNEL balance is never "accidentally sent" — it is the core distribution asset.

```solidity
function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
    if (_token == address(kernel)) revert CannotWithdrawDistributionToken();
    // ... rest of function
}
```

---

### Proof of Concept

1. Owner deploys `KernelTop100MerkleDistributor` with a merkle root covering 1,000 users each entitled to 1,000 KERNEL, total 1,000,000 KERNEL deposited.
2. `vestingStartTimestamp` passes; 20 days elapse. Two-thirds of all tokens (≈666,666 KERNEL) are now vested and owed to users.
3. No user has called `claim()` yet.
4. Owner calls:
   ```solidity
   withdrawTokens(address(kernel), 1_000_000e18, ownerAddress);
   ```
5. All 1,000,000 KERNEL are transferred to the owner.
6. Every user who calls `claim()` receives a revert (ERC20 transfer fails — insufficient balance).
7. All 666,666 vested KERNEL are permanently stolen; the remaining 333,333 unvested KERNEL are also gone. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-338)
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
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-472)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);

        emit TokensWithdrawn(_token, _amount, _recipient);
    }
```
