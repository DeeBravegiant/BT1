### Title
Zero-Amount Fee Transfer in `claim()` Freezes User Rewards When `feeInBPS == 0` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `fee == 0`. When `feeInBPS` is set to zero (which is explicitly permitted), this produces a zero-value transfer that reverts for tokens that do not allow zero-amount transfers, permanently freezing all user claims.

### Finding Description
In `MerkleDistributor.sol`, the `claim()` function computes a fee and then unconditionally transfers it to `protocolTreasury`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

// Send the fee to the protocol treasury
IERC20(token).safeTransfer(protocolTreasury, fee);   // ← always called, even when fee == 0
``` [1](#0-0) 

Both `initialize()` and `setFeeInBPS()` permit `feeInBPS == 0` — the only guard is `> MAX_FEE_IN_BPS`:

```solidity
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) {
        revert InvalidFeeInBPS();
    }
    feeInBPS = _feeInBPS;
    ...
}
``` [2](#0-1) 

When `feeInBPS == 0`, `fee` evaluates to `0` for every claimant, and the unconditional `safeTransfer(protocolTreasury, 0)` will revert for any ERC-20 token that disallows zero-value transfers (a well-documented class of tokens: https://github.com/d-xo/weird-erc20#revert-on-zero-value-transfers). Because `claim()` is the only way for users to receive their allocated tokens, every user's claim is permanently blocked.

By contrast, the sibling contract `KernelTop100MerkleDistributor` correctly guards the fee transfer:

```solidity
if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);
}
kernel.safeTransfer(user, amountToSend);
``` [3](#0-2) 

`MerkleDistributor` lacks this guard entirely.

### Impact Explanation
Any user attempting to call `claim()` while `feeInBPS == 0` and the distributed token reverts on zero-value transfers will have their transaction revert. Since `claim()` is the sole withdrawal path, all unclaimed token allocations are permanently frozen in the contract. This matches **Medium — Permanent freezing of unclaimed yield**.

### Likelihood Explanation
- `feeInBPS == 0` is a valid and reachable state: it is the default if initialized with `_feeInBPS = 0`, and the owner can set it to zero at any time via `setFeeInBPS(0)`.
- The `MerkleDistributor` is token-agnostic (`token` can be changed by the owner via `setToken()`), so it may be configured with tokens that revert on zero-value transfers.
- No privileged attacker is required; the condition arises from normal, permitted configuration.

### Recommendation
Guard the fee transfer with a zero-amount check, consistent with `KernelTop100MerkleDistributor`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

### Proof of Concept
1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers and `feeInBPS = 0` (or call `setFeeInBPS(0)` after deployment).
2. Set a valid Merkle root containing an allocation for `alice`.
3. `alice` calls `claim(index, alice, cumulativeAmount, proof)`.
4. `fee = (claimableAmount * 0) / 10_000 = 0`.
5. `IERC20(token).safeTransfer(alice, claimableAmount)` succeeds.
6. `IERC20(token).safeTransfer(protocolTreasury, 0)` **reverts** — the entire transaction is rolled back.
7. `alice`'s allocation remains locked in the contract indefinitely; no alternative claim path exists. [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L96-147)
```text
    /// @inheritdoc IMerkleDistributor
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        whenNotPaused
    {
        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim info, and send the token.
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);

        emit Claimed(index, account, claimableAmount);
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L198-205)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        feeInBPS = _feeInBPS;

        emit FeeInBPSUpdated(_feeInBPS);
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L331-335)
```text
        // Transfer tokens
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
        kernel.safeTransfer(user, amountToSend);
```
