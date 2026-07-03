Audit Report

## Title
Unconditional zero-value `safeTransfer` of fee in `MerkleDistributor.claim()` blocks all claims for tokens that revert on zero transfers - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` at line 144 without checking whether `fee` is zero. When `claimableAmount * feeInBPS < 10_000`, integer division truncates `fee` to `0`, and for ERC20 tokens that revert on zero-value transfers, the entire `claim()` call reverts. The sibling contract `KernelMerkleDistributor` correctly guards this transfer with `if (fee > 0)`, confirming this is an oversight in `MerkleDistributor`.

## Finding Description
In `MerkleDistributor.claim()`, the fee is computed and transferred unconditionally:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol L138-144
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);  // no zero-check
``` [1](#0-0) 

When `claimableAmount * feeInBPS < 10_000`, `fee` truncates to `0`. The subsequent `safeTransfer(protocolTreasury, 0)` is called unconditionally. For tokens that revert on zero-value transfers, the entire transaction reverts, preventing the user from claiming their entitled yield.

Additionally, `setFeeInBPS` permits `_feeInBPS = 0` (only enforces an upper bound), meaning `fee` is always `0` in that configuration, causing every single claim to fail for such tokens. [2](#0-1) 

`KernelMerkleDistributor._processClaim()` correctly guards the same transfer:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol L341-343
if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);
}
``` [3](#0-2) 

This asymmetry confirms the missing guard in `MerkleDistributor` is an oversight, not a design choice.

## Impact Explanation
**Medium. Permanent freezing of unclaimed yield.** Users with `claimableAmount * feeInBPS < 10_000` cannot receive tokens they are entitled to per the Merkle proof. If `feeInBPS == 0`, all claims for all users fail permanently. The user's state is not updated (the revert unwinds the transaction), but they remain unable to claim until their cumulative allocation grows sufficiently — and if `feeInBPS == 0`, no amount ever produces a non-zero fee, making the freeze permanent for all claimants.

## Likelihood Explanation
The `token` field is admin-configurable and can be any ERC20; several widely-used tokens (e.g., BNB) revert on zero-value transfers. The condition `claimableAmount * feeInBPS < 10_000` is easily satisfied: with `feeInBPS = 1`, any `claimableAmount < 10_000` produces `fee = 0`. No privilege is required to trigger the bug — any reward claimant calling `claim()` with a qualifying amount hits it. The `feeInBPS = 0` case makes it universal.

## Recommendation
Add a zero-check before the fee transfer, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

## Proof of Concept
1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers and `feeInBPS = 100` (1%).
2. Construct a Merkle tree where a user has `cumulativeAmount = 50`. `fee = (50 * 100) / 10_000 = 0`.
3. User calls `claim()`. Execution reaches `IERC20(token).safeTransfer(protocolTreasury, 0)` at line 144.
4. The token reverts on the zero-value transfer; the entire transaction reverts.
5. The user's `userClaims` state is not updated (revert unwinds lines 134–135), but the user remains unable to claim until their allocation exceeds `10_000 / feeInBPS = 100` units.
6. With `feeInBPS = 0`: deploy with `_feeInBPS = 0` (passes the `> MAX_FEE_IN_BPS` check), then any `claim()` call produces `fee = 0` and reverts for all users unconditionally.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L198-201)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L341-343)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```
