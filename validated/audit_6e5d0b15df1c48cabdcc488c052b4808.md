### Title
Owner Can Drain All User-Allocated KERNEL Tokens from Merkle Distributor - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

### Summary
The `KernelTop100MerkleDistributor` contract holds KERNEL tokens pre-funded for distribution to eligible users via a merkle-proof vesting schedule. The contract owner can call `withdrawTokens()` with no restriction on token type or amount, allowing them to drain the entire KERNEL balance — including all tokens allocated to users who have not yet claimed — before any user can receive their allocation.

### Finding Description
`KernelTop100MerkleDistributor.withdrawTokens()` is an unrestricted admin sweep function:

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
``` [1](#0-0) 

There is no guard preventing `_token` from being the `kernel` distribution token itself, no check that the remaining balance covers outstanding user allocations, and no time-lock or multi-sig requirement. The contract is funded with KERNEL tokens that users are entitled to claim gradually over a 30-day vesting window (`VESTING_DURATION = 30 days`). [2](#0-1) 

Users prove their allocation via `claim()` or `claimAndStake()`, both of which transfer from the contract's KERNEL balance: [3](#0-2) 

The sister contract `KernelMerkleDistributor` does **not** expose a `withdrawTokens` function, making this a unique structural flaw in `KernelTop100MerkleDistributor`. [4](#0-3) 

### Impact Explanation
**Critical — Direct theft of user funds.**

The owner can call `withdrawTokens(address(kernel), IERC20(kernel).balanceOf(address(this)), ownerAddress)` in a single transaction, draining the entire KERNEL balance. Every user with a valid merkle allocation who has not yet claimed (or has only partially claimed during the vesting period) permanently loses their entitled tokens. The vesting schedule and merkle proof system become meaningless because the underlying token pool can be emptied at will.

### Likelihood Explanation
**Medium.** The attack requires the owner key to be malicious or compromised. However:
- The contract is `OwnableUpgradeable` with a single EOA owner — no multisig or timelock is enforced at the contract level.
- The function is callable at any time, including immediately after the contract is funded and before vesting begins.
- The `KernelTop100MerkleDistributor` is specifically designed for a "Top 100" distribution, implying a large, concentrated KERNEL allocation, making it a high-value target. [5](#0-4) 

### Recommendation
1. **Disallow withdrawing the primary distribution token**: Add a check `require(_token != address(kernel), "Cannot withdraw distribution token")`.
2. **Alternatively, enforce a minimum reserve**: Before transferring, verify `IERC20(kernel).balanceOf(address(this)) - _amount >= totalAllocated - totalClaimed`.
3. **Governance controls**: Protect `withdrawTokens` with a timelock or require multisig authorization, consistent with the recommendation in the reference report.
4. **Align with `KernelMerkleDistributor`**: That contract omits `withdrawTokens` entirely; consider the same approach for `KernelTop100MerkleDistributor`.

### Proof of Concept
```solidity
// Assume owner is malicious or compromised
// Contract holds 1,000,000 KERNEL for Top-100 user distribution

address kernelToken = address(distributor.kernel());
uint256 balance = IERC20(kernelToken).balanceOf(address(distributor));

// Owner drains entire distribution pool in one call
distributor.connect(owner).withdrawTokens(kernelToken, balance, owner.address);

// All 100 eligible users now receive 0 tokens when they call claim()
// claim() reverts with NoTokensToClaim or ERC20 insufficient balance
``` [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L111-117)
```text
contract KernelTop100MerkleDistributor is
    IMerkleDistributor,
    Initializable,
    OwnableUpgradeable,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L126-127)
```text
    /// @notice The vesting duration in seconds (30 days)
    uint256 public constant VESTING_DURATION = 30 days;
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L455-472)
```text
    /**
     * @notice Allows the owner to withdraw tokens from the contract
     * @param _token The token to withdraw
     * @param _amount The amount to withdraw
     * @param _recipient The recipient of the tokens
     */
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L415-424)
```text
    /// @dev Pauses the contract
    function pause() external onlyOwner {
        _pause();
    }

    /// @dev Unpauses the contract
    function unpause() external onlyOwner {
        _unpause();
    }
}
```
