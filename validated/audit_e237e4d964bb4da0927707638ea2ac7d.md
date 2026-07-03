### Title
Admin `withdrawTokens()` Drains KERNEL Distribution Balance Without Updating Vesting State — (File: `contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

---

### Summary

`KernelTop100MerkleDistributor.withdrawTokens()` allows the owner to transfer any ERC20 token — including the KERNEL distribution token — out of the contract without updating any vesting accounting state. Because `_getUnclaimedVestedAmount()` computes claimable amounts purely from time elapsed and `userClaims[user].amountClaimed`, it remains unaware of the reduced balance. Any user whose vested tokens have not yet been claimed will receive a revert on transfer, permanently freezing their unclaimed yield.

---

### Finding Description

The `withdrawTokens()` function is a general token-recovery helper: [1](#0-0) 

It performs no check on whether `_token` is the KERNEL distribution token, and it updates none of the vesting state variables:

- `vestingStartTimestamp` — unchanged; the vesting schedule still promises the full allocation.
- `merkleRoot` — unchanged; every user's merkle-proven entitlement is still valid.
- `userClaims[user].amountClaimed` — unchanged; the contract still believes users have only claimed what they previously withdrew.

The vesting math in `_getUnclaimedVestedAmount()` is entirely time-based: [2](#0-1) 

It never inspects `kernel.balanceOf(address(this))`. After a `withdrawTokens` call that removes KERNEL tokens, the computed `claimableAmount` in `claim()` / `claimAndStake()` will exceed the contract's actual balance, causing `safeTransfer` to revert: [3](#0-2) 

This is structurally identical to the reported `ActivityRewardDistributor.withdrawPmx()` pattern: a privileged withdrawal reduces the token balance while leaving the reward-accounting state intact, so subsequent user claims fail.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

All users whose vested KERNEL tokens have not yet been claimed will be unable to do so. The contract holds no mechanism to recalculate or reduce outstanding entitlements after a withdrawal, so the frozen state is permanent unless the owner re-deposits the exact shortfall.

---

### Likelihood Explanation

**Low-Medium.** The `withdrawTokens` function is a standard token-rescue pattern that owners routinely call to recover accidentally sent tokens or to sweep residual balances after a distribution ends. An owner acting in good faith — believing the vesting period is over or that the contract holds surplus KERNEL — can inadvertently drain tokens still owed to users. No malicious intent is required; a mistaken amount or premature call is sufficient.

---

### Recommendation

1. **Block withdrawal of the distribution token during the active vesting window.** Add a guard:
   ```solidity
   if (_token == address(kernel) && block.timestamp < vestingStartTimestamp + VESTING_DURATION) {
       revert CannotWithdrawDistributionToken();
   }
   ```
2. **After the vesting window**, only allow withdrawal of the genuinely unclaimed surplus (i.e., `kernel.balanceOf(address(this))` minus the sum of all remaining entitlements), not an arbitrary amount.

---

### Proof of Concept

1. Contract is initialized with 1 000 KERNEL for 10 users (100 KERNEL each), `vestingStartTimestamp = T`, `VESTING_DURATION = 30 days`.
2. At `T + 15 days` (50 % vested), each user is entitled to 50 KERNEL; none have claimed yet.
3. Owner calls `withdrawTokens(kernelAddress, 600, treasury)`, believing 600 tokens are surplus. Contract balance drops to 400 KERNEL.
4. All 10 users call `claim()`. `_getUnclaimedVestedAmount` returns 50 for each. The first 8 succeed (400 KERNEL transferred). Users 9 and 10 receive a revert from `safeTransfer` — their 100 KERNEL of unclaimed yield is permanently frozen. [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L235-273)
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
    }
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
