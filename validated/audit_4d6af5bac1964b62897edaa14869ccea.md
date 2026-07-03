### Title
Unconditional zero-value `safeTransfer` of fee in `MerkleDistributor.claim()` blocks all claims for tokens that revert on zero transfers - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `fee` rounds down to zero due to integer division. For ERC20 tokens that revert on zero-value transfers, this permanently blocks any user whose claimable amount is too small to produce a non-zero fee, freezing their entitled yield.

### Finding Description
In `MerkleDistributor.claim()`, the fee is computed as:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);   // no zero-check
```

When `claimableAmount * feeInBPS < 10_000`, integer division truncates `fee` to `0`. The subsequent `safeTransfer(protocolTreasury, 0)` is then called unconditionally. [1](#0-0) 

For any ERC20 token that reverts on zero-value transfers (a well-known class of tokens), this causes the entire `claim()` transaction to revert. The user's claim state is updated before the transfers (lines 134–135), but since the revert unwinds the whole transaction, the state is not persisted — however the user remains permanently unable to claim small amounts.

Additionally, `feeInBPS` is allowed to be set to `0` (no lower-bound check in `setFeeInBPS`), which means `fee` is always `0`, making every single claim fail for such tokens regardless of amount. [2](#0-1) 

The sibling contract `KernelMerkleDistributor` correctly guards the transfer with `if (fee > 0)`, confirming this is an oversight in `MerkleDistributor`: [3](#0-2) 

### Impact Explanation
Any user attempting to claim tokens from `MerkleDistributor` when the distributed token reverts on zero-value transfers will have their claim permanently blocked if their `claimableAmount * feeInBPS < 10_000`. If `feeInBPS == 0`, all claims fail for all users. This constitutes **temporary (or permanent) freezing of unclaimed yield** — users cannot receive tokens they are entitled to per the Merkle proof.

### Likelihood Explanation
The `MerkleDistributor` is a generic distributor whose `token` is set by the admin and can be any ERC20. Several widely-used tokens (e.g., LIDO's stETH in certain configurations, BNB, and others) revert on zero-value transfers. The condition `claimableAmount * feeInBPS < 10_000` is easily satisfied for users with small allocations or when `feeInBPS` is low (e.g., `feeInBPS = 1` means any `claimableAmount < 10_000` produces `fee = 0`). The entry path requires no privilege — any reward claimant calling `claim()` triggers it.

### Recommendation
Add a zero-check before the fee transfer, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

### Proof of Concept
1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers and `feeInBPS = 100` (1%).
2. A user has a `claimableAmount = 50` (in token's smallest unit). `fee = (50 * 100) / 10_000 = 0`.
3. User calls `claim()`. Execution reaches `IERC20(token).safeTransfer(protocolTreasury, 0)`.
4. The token reverts. The user's claim fails entirely.
5. The user cannot claim until their cumulative allocation grows to at least `10_000 / feeInBPS = 100` units — but if `feeInBPS = 0`, no amount ever produces a non-zero fee and all claims are permanently blocked. [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-146)
```text
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
