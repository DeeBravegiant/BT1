### Title
Unconditional Zero-Amount `safeTransfer` to `protocolTreasury` Permanently Freezes All Claims When `feeInBPS == 0` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.sol`'s `claim()` function unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `fee` is zero. When `feeInBPS` is set to `0` by the owner, `fee` computes to `0` for every claim, and any token that reverts on zero-value transfers will cause every `claim()` call to revert, permanently blocking all users from withdrawing their entitled tokens.

### Finding Description
In `MerkleDistributor.sol`, the `claim()` function computes a fee and then unconditionally transfers it to `protocolTreasury`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

// Send the fee to the protocol treasury
IERC20(token).safeTransfer(protocolTreasury, fee);   // ← no zero-check
``` [1](#0-0) 

The owner can set `feeInBPS` to `0` via `setFeeInBPS(0)`: [2](#0-1) 

When `feeInBPS == 0`, `fee == 0` for every claim, and `IERC20(token).safeTransfer(protocolTreasury, 0)` is executed unconditionally. Tokens such as BNB, LEND, and other non-standard ERC20s revert on zero-value transfers. The `KernelMerkleDistributor` correctly guards this transfer with `if (fee > 0)`: [3](#0-2) 

`MerkleDistributor` lacks this guard entirely.

### Impact Explanation
When `feeInBPS == 0` and the distributed token reverts on zero-value transfers, every call to `claim()` reverts at the unconditional `safeTransfer(protocolTreasury, 0)` line. No user can claim any tokens. The tokens remain locked in the contract with no recovery path for users (only the owner can call `setToken` or `setFeeInBPS`, but setting `feeInBPS > 0` to work around the bug would incorrectly charge users a fee they were not supposed to pay). This constitutes **permanent freezing of unclaimed yield** for all claimants.

### Likelihood Explanation
`feeInBPS == 0` is a natural and expected configuration (zero-fee distribution). The `MerkleDistributor` is a generic contract whose `token` is set by the owner and can be any ERC20. The combination of a zero-fee configuration and a token that reverts on zero-value transfers is realistic and requires no attacker action — any user calling `claim()` triggers the revert.

### Recommendation
Add a zero-amount guard before the fee transfer, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
``` [4](#0-3) 

### Proof of Concept
1. Owner deploys `MerkleDistributor` with a token `T` that reverts on zero-value transfers (e.g., BNB).
2. Owner calls `setFeeInBPS(0)` — a zero-fee configuration.
3. A merkle root is set; users are entitled to claim tokens.
4. User calls `claim(index, account, cumulativeAmount, merkleProof)` with a valid proof.
5. `claimableAmount > 0`, `fee = (claimableAmount * 0) / 10_000 = 0`, `amountToSend = claimableAmount`.
6. `IERC20(T).safeTransfer(account, amountToSend)` succeeds.
7. `IERC20(T).safeTransfer(protocolTreasury, 0)` reverts because token `T` does not allow zero-value transfers.
8. The entire transaction reverts. The user receives nothing. All subsequent `claim()` calls by any user revert identically. All claimable tokens are permanently frozen in the contract. [5](#0-4)

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L341-343)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```
