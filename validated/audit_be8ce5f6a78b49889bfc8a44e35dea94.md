### Title
Owner Can Drain All Undistributed KERNEL Tokens, Permanently Freezing All Users' Unclaimed Vested Yield - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

### Summary
`KernelTop100MerkleDistributor.withdrawTokens` is callable by the owner at any time with no restrictions on token address or amount. The owner can drain the entire KERNEL balance held by the contract, permanently preventing all users from claiming their vested token allocations.

### Finding Description
`KernelTop100MerkleDistributor` holds KERNEL tokens that vest linearly over a 30-day period and are distributed to users via merkle-proof claims. The contract exposes an unrestricted `withdrawTokens` function:

```solidity
function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
    UtilLib.checkNonZeroAddress(_token);
    UtilLib.checkNonZeroAddress(_recipient);
    if (_amount == 0) revert ZeroValueProvided();
    IERC20(_token).safeTransfer(_recipient, _amount);
    emit TokensWithdrawn(_token, _amount, _recipient);
}
```

There is no guard preventing `_token` from being the `kernel` distribution token, no cap on `_amount`, and no check that the withdrawal leaves sufficient balance to cover outstanding user allocations. The owner can call `withdrawTokens(address(kernel), kernel.balanceOf(address(this)), anyAddress)` at any time, transferring the entire KERNEL reserve to an arbitrary recipient.

After such a call, every user who attempts to call `claim` or `claimAndStake` will have their `safeTransfer` revert due to insufficient balance, even though their vested allocation is non-zero per `_getUnclaimedVestedAmount`. [1](#0-0) 

### Impact Explanation
All users with unclaimed vested KERNEL tokens lose access to their yield permanently. The tokens are not burned — they are transferred to an owner-controlled address — making this a direct theft of unclaimed yield. Every user in the merkle distribution is affected simultaneously. Impact: **High — theft of unclaimed yield**. [2](#0-1) 

### Likelihood Explanation
The function requires no preconditions beyond ownership. It can be called at any point during or after the 30-day vesting window. The most damaging window is near the end of vesting, when the maximum amount of tokens has accrued but not yet been claimed. No external dependency, oracle, or secondary condition is needed.

### Recommendation
Restrict `withdrawTokens` so it cannot withdraw the `kernel` distribution token, or add a check that the remaining `kernel` balance after withdrawal is at least equal to the total outstanding unclaimed allocation. A simpler fix is to remove the ability to withdraw `kernel` entirely and only allow recovery of accidentally sent foreign tokens:

```solidity
function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
    if (_token == address(kernel)) revert CannotWithdrawDistributionToken();
    // ... rest unchanged
}
```

### Proof of Concept
1. Owner deploys `KernelTop100MerkleDistributor` with a merkle root covering 100 users, each allocated 1,000 KERNEL. Contract holds 100,000 KERNEL.
2. Vesting begins. After 15 days (50% of the 30-day window), each user has ~500 KERNEL vested but unclaimed.
3. Owner calls `withdrawTokens(address(kernel), 100_000e18, ownerAddress)`.
4. Contract KERNEL balance drops to 0.
5. Any user calling `claim(1000e18, proof)` hits `kernel.safeTransfer(user, 500e18)` which reverts with `ERC20InsufficientBalance`.
6. All 50,000 KERNEL of accrued-but-unclaimed yield is permanently inaccessible to users and held by the owner. [3](#0-2) [1](#0-0)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L260-272)
```text
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
