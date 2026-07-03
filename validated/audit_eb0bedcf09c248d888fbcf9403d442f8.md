Audit Report

## Title
Unconditional Zero-Value Fee Transfer in `MerkleDistributor.claim()` Freezes User Claims When `feeInBPS == 0` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `fee == 0`. For ERC20 tokens that revert on zero-value transfers, this permanently blocks all users from claiming their allocated tokens whenever `feeInBPS` is set to zero. The sibling contract `KernelMerkleDistributor` correctly guards this transfer with `if (fee > 0)`, confirming the omission in `MerkleDistributor`.

## Finding Description
In `MerkleDistributor.claim()`, after computing the fee at line 138, the protocol unconditionally executes the fee transfer at line 144 regardless of whether `fee` is zero:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);  // ← no zero-check
``` [1](#0-0) 

`feeInBPS` has no lower-bound check at initialization or in `setFeeInBPS()`, so `0` is a valid and reachable value: [2](#0-1) 

The contract is explicitly generic — `token` can be set to any ERC20 via `setToken()`: [3](#0-2) 

By contrast, `KernelMerkleDistributor._processClaim()` correctly guards the fee transfer: [4](#0-3) 

When `feeInBPS == 0` and the distributed token reverts on zero-value transfers (e.g., LEND, cUSDCv3), every `claim()` call reverts after the user transfer succeeds but before state is finalized — actually, state is updated before the transfers (lines 134–135), so the user's `lastClaimedIndex` and `cumulativeAmount` are updated, but the transaction reverts rolling back all state. The user cannot claim. [5](#0-4) 

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** When `feeInBPS == 0` and the token reverts on zero-value transfers, no user can successfully call `claim()`. All allocated yield remains locked in the distributor. The only remediation path for the owner is to call `setFeeInBPS` with a non-zero value, which imposes an unintended fee on all subsequent claimants — an unacceptable outcome for a zero-fee operational state.

## Likelihood Explanation
- `feeInBPS = 0` is a valid and explicitly reachable state: no on-chain restriction prevents it at initialization or via `setFeeInBPS(0)`.
- The contract is generic and designed to distribute arbitrary ERC20 tokens via `setToken()`, making it realistic that a token reverting on zero-value transfers is used.
- Any claimant calling `claim()` with a valid merkle proof triggers the revert — no special preconditions beyond `feeInBPS == 0` and a zero-revert token.
- The inconsistency with sibling contracts confirms this is an unintentional omission, not a deliberate design choice.

## Recommendation
Add a zero-check before the fee transfer, consistent with `KernelMerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

## Proof of Concept
1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers (e.g., a mock ERC20 with `require(amount > 0)`).
2. Call `setFeeInBPS(0)`.
3. Call `setMerkleRoot(validRoot)` and fund the contract.
4. Call `claim(index, account, cumulativeAmount, proof)` with a valid proof.
5. Observe: `fee = 0`, `IERC20(token).safeTransfer(account, claimableAmount)` succeeds, then `IERC20(token).safeTransfer(protocolTreasury, 0)` reverts, rolling back the entire transaction.
6. The user's claim state is not persisted; the user cannot claim their tokens.
7. All users are blocked until the owner sets `feeInBPS` to a non-zero value.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L133-146)
```text
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
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L185-193)
```text
    function setToken(address _token) external onlyOwner {
        if (_token == address(0)) {
            revert ZeroValueProvided();
        }

        token = _token;

        emit TokenUpdated(_token);
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
