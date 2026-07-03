### Title
Unconditional Zero-Value Fee Transfer in `MerkleDistributor.claim()` Freezes User Claims When `feeInBPS == 0` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `fee == 0`. For ERC20 tokens that revert on zero-value transfers, this causes every `claim()` call to revert whenever `feeInBPS` is set to zero, permanently freezing all unclaimed yield in the distributor until the owner intervenes.

### Finding Description
In `MerkleDistributor.claim()`, after computing the fee, the protocol unconditionally transfers the fee amount to `protocolTreasury` regardless of whether the fee is zero:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

// Send the fee to the protocol treasury
IERC20(token).safeTransfer(protocolTreasury, fee);  // ← called even when fee == 0
```

When `feeInBPS == 0`, `fee` evaluates to `0`, and `IERC20(token).safeTransfer(protocolTreasury, 0)` is executed. Numerous ERC20 tokens (e.g., LEND, cUSDCv3, and others) revert on zero-value transfers. In such cases, every `claim()` call reverts, making it impossible for any user to claim their allocated tokens.

The sibling contracts `KernelMerkleDistributor` and `KernelTop100MerkleDistributor` correctly guard the fee transfer:

```solidity
if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);
}
```

`MerkleDistributor` is missing this guard entirely.

`feeInBPS` can be set to `0` both at initialization (no lower-bound check) and via `setFeeInBPS(0)` (only upper-bound `> MAX_FEE_IN_BPS` is checked). This is a realistic operational state — a protocol may legitimately want to run with zero fees.

### Impact Explanation
When `feeInBPS == 0` and the distributed token reverts on zero-value transfers, the `claim()` function is permanently bricked for all users. No user can receive their allocated tokens. This constitutes **permanent freezing of unclaimed yield** (Medium) until the owner calls `setFeeInBPS` with a non-zero value — an action that itself imposes an unintended fee on users who then claim.

### Likelihood Explanation
- `feeInBPS` can be set to `0` by the owner at any time with no on-chain restriction.
- The `MerkleDistributor` is a generic contract whose `token` can be set to any ERC20 via `setToken()`, including tokens that revert on zero-value transfers.
- Any reward claimant calling `claim()` triggers the path with no special preconditions beyond `feeInBPS == 0`.

### Recommendation
Add a zero-check before the fee transfer, consistent with `KernelMerkleDistributor` and `KernelTop100MerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

### Proof of Concept
1. Owner deploys `MerkleDistributor` with a token that reverts on zero-value transfers (e.g., a cToken variant).
2. Owner calls `setFeeInBPS(0)` to remove the protocol fee.
3. Owner sets a valid merkle root.
4. User calls `claim(index, account, cumulativeAmount, merkleProof)`.
5. `fee = (claimableAmount * 0) / 10_000 = 0`.
6. `IERC20(token).safeTransfer(account, claimableAmount)` succeeds.
7. `IERC20(token).safeTransfer(protocolTreasury, 0)` reverts.
8. The entire transaction reverts; the user's claim state is not updated (state changes are rolled back), but the user cannot claim their tokens.
9. All users are blocked from claiming until the owner sets `feeInBPS` to a non-zero value. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L71-87)
```text
    function initialize(address token_, address _protocolTreasury, uint256 _feeInBPS) external initializer {
        // token can be set later but not the protocol treasury
        if (_protocolTreasury == address(0)) {
            revert ZeroValueProvided();
        }

        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        __Ownable_init();
        __Pausable_init();

        token = token_;
        protocolTreasury = _protocolTreasury;
        feeInBPS = _feeInBPS;
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L137-146)
```text
        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);

        emit Claimed(index, account, claimableAmount);
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L341-343)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```
