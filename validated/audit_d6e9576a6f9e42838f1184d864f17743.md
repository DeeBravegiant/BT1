### Title
Owner Can Drain Vested-But-Unclaimed KERNEL Tokens via Unrestricted `withdrawTokens` — (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

---

### Summary

`KernelTop100MerkleDistributor.withdrawTokens` imposes no restriction on withdrawing the KERNEL token itself, allowing the owner to drain the entire contract balance — including tokens that have already vested and are owed to users but not yet claimed. This is the direct analog of the `VestingWallet.revoke` bug: a privileged function that transfers the full balance instead of only the portion that is legitimately recoupable.

---

### Finding Description

`KernelTop100MerkleDistributor` holds KERNEL tokens and releases them to eligible users over a 30-day linear vesting schedule. Users prove eligibility via a Merkle proof and call `claim()` or `claimAndStake()` to receive their pro-rata vested share.

The contract also exposes an admin-only `withdrawTokens` function:

```solidity
// contracts/KERNEL/KernelTop100MerkleDistributor.sol L461-472
function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
    UtilLib.checkNonZeroAddress(_token);
    UtilLib.checkNonZeroAddress(_recipient);

    if (_amount == 0) {
        revert ZeroValueProvided();
    }

    IERC20(_token).safeTransfer(_recipient, _amount);   // ← no guard on user-owed balance

    emit TokensWithdrawn(_token, _amount, _recipient);
}
``` [1](#0-0) 

There is **no check** that prevents `_token == address(kernel)` or that caps `_amount` to the portion of the balance that is not yet owed to any user. The contract tracks per-user `amountClaimed` but never aggregates total outstanding obligations: [2](#0-1) 

The vesting calculation in `_getUnclaimedVestedAmount` correctly computes what each user is owed at any point in time: [3](#0-2) 

But `withdrawTokens` is completely decoupled from this accounting. The owner can call it with `_token = kernel` and `_amount = kernel.balanceOf(address(this))` at any point — including mid-vesting — and sweep all remaining KERNEL out of the contract, leaving users with nothing to claim.

The owner can also first call `pause()` to block user claims, then drain the contract: [4](#0-3) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

KERNEL tokens held by this contract are yield/rewards allocated to the top-100 eligible users. Once the owner calls `withdrawTokens(kernel, kernel.balanceOf(address(this)), recipient)`, all vested-but-unclaimed KERNEL is permanently removed. Users who call `claim()` afterward will receive a `SafeERC20` revert (insufficient balance), permanently losing their entitled yield. The loss is proportional to the total undistributed KERNEL balance at the time of the call.

---

### Likelihood Explanation

**Low.** The call requires the contract owner to act — either maliciously or by mistake (e.g., believing they are recovering "leftover" tokens after vesting ends, while users still have unclaimed vested amounts). The function is not gated by any time-lock or secondary approval. The absence of any accounting guard means even a well-intentioned but mistimed call causes irreversible loss. No external attacker path exists; the risk is owner-side misuse of an incorrectly implemented admin function.

---

### Recommendation

Mirror the fix proposed in the external report: before allowing withdrawal of the KERNEL token, compute the total outstanding obligation across all users and cap the withdrawable amount to `kernel.balanceOf(address(this)) - totalOutstandingObligations`. Concretely:

1. Track a `totalAllocated` state variable (sum of all Merkle-leaf amounts loaded at initialization) and a `totalClaimed` counter incremented on every successful `claim`/`claimAndStake`.
2. In `withdrawTokens`, when `_token == address(kernel)`, enforce:
   ```solidity
   uint256 owed = totalAllocated - totalClaimed;
   require(kernel.balanceOf(address(this)) - _amount >= owed, "Would drain user funds");
   ```
3. Alternatively, restrict `withdrawTokens` to non-KERNEL tokens entirely and add a separate, time-locked `recoverUnallocatedKernel` function callable only after `vestingStartTimestamp + VESTING_DURATION`.

---

### Proof of Concept

1. Protocol deploys `KernelTop100MerkleDistributor` with 1,000,000 KERNEL for 100 users (10,000 each).
2. Vesting starts; 15 days pass. Each user has 50% vested (5,000 KERNEL each) but none have claimed yet.
3. Owner calls:
   ```solidity
   withdrawTokens(address(kernel), 1_000_000e18, ownerAddress);
   ``` [1](#0-0) 
4. The full 1,000,000 KERNEL is transferred to the owner. The contract balance is now 0.
5. Any user who calls `claim()` receives a revert — their 5,000 vested KERNEL is permanently lost.

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L152-158)
```text
    struct UserClaim {
        uint256 lastClaimTimestamp;
        uint256 amountClaimed;
    }

    /// @notice The user claims mapping
    mapping(address user => UserClaim userClaim) public userClaims;
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L474-477)
```text
    /// @notice Pauses the contract
    function pause() external onlyOwner {
        _pause();
    }
```
