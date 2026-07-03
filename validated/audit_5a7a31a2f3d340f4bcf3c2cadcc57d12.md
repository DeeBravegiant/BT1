### Title
Unconditional Zero-Value Fee Transfer in `MerkleDistributor.claim()` Causes Temporary Freezing of User Funds for Tokens Reverting on Zero Transfers - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `fee == 0` (i.e., when `feeInBPS == 0`). For ERC20 tokens that revert on zero-value transfers (e.g., USDT), this causes every `claim()` call to revert, temporarily freezing all user claimable funds in the contract.

### Finding Description
In `contracts/utils/MerkleDistributor/MerkleDistributor.sol`, the `initialize()` function explicitly permits `feeInBPS = 0` — there is no minimum-fee check — and the comment even notes that `token` can be set post-deployment: [1](#0-0) 

Inside `claim()`, the fee is computed and then transferred to `protocolTreasury` unconditionally, with no `if (fee > 0)` guard: [2](#0-1) 

When `feeInBPS == 0`, `fee == 0`, and `IERC20(token).safeTransfer(protocolTreasury, 0)` is executed. USDT (and other tokens) revert on zero-value transfers. Because this call follows the user's transfer on line 141, the entire transaction reverts atomically — the user receives nothing and the claim is not recorded as completed.

This is a direct structural inconsistency with the sibling distributor contracts in the same repository. Both `KernelMerkleDistributor` and `KernelTop100MerkleDistributor` correctly guard the fee transfer: [3](#0-2) [4](#0-3) 

`MerkleDistributor` lacks this guard entirely.

### Impact Explanation
Any user calling `claim()` on a `MerkleDistributor` instance deployed with `feeInBPS = 0` and a token that reverts on zero-value transfers will have their transaction revert. All claimable funds are temporarily frozen in the contract. To unblock claims, the owner must call `setFeeInBPS()` with a non-zero value, which imposes an unintended fee on users or forces a configuration change that was never planned. This maps to **Medium — Temporary freezing of funds**.

### Likelihood Explanation
`MerkleDistributor` is a generic, reusable contract. `feeInBPS = 0` is a natural and explicitly supported deployment choice (no validation prevents it). USDT is one of the most commonly distributed reward tokens in DeFi. The combination is realistic and requires no privileged attacker — any reward claimant triggers the revert simply by calling `claim()`.

### Recommendation
Add a zero-fee guard before the treasury transfer, consistent with the pattern already used in `KernelMerkleDistributor` and `KernelTop100MerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

### Proof of Concept
1. Deploy `MerkleDistributor` with `feeInBPS = 0` and `token = USDT`.
2. Owner calls `setMerkleRoot()` with a valid root.
3. User calls `claim(index, account, cumulativeAmount, proof)` with a valid Merkle proof.
4. Execution reaches line 144: `IERC20(USDT).safeTransfer(protocolTreasury, 0)`.
5. USDT reverts on the zero-value transfer; the entire transaction reverts.
6. The user's `lastClaimedIndex` is not updated; the user cannot receive their tokens.
7. All subsequent `claim()` calls by any user revert identically until the owner raises `feeInBPS` above zero.

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L341-343)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L367-369)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```
