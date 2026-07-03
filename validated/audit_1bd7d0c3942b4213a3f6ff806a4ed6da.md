Audit Report

## Title
Unconditional zero-value fee transfer in `MerkleDistributor.claim()` permanently blocks claims for tokens that revert on zero transfers - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` at line 144 even when `fee` evaluates to zero. For ERC-20 tokens that revert on zero-value transfers, every `claim()` call reverts, permanently preventing users from claiming their yield. The sibling contract `KernelMerkleDistributor._processClaim()` already applies the correct `if (fee > 0)` guard, confirming the team is aware of this pattern but did not apply it consistently.

## Finding Description
In `MerkleDistributor.claim()` (lines 138–144):

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);  // unconditional
```

`fee` evaluates to zero in two realistic paths:
1. `feeInBPS == 0` — `setFeeInBPS` only enforces `_feeInBPS <= MAX_FEE_IN_BPS` (line 199), so zero is explicitly permitted.
2. Integer truncation — e.g., `feeInBPS = 1`, `claimableAmount = 50` → `fee = (50 * 1) / 10_000 = 0`.

When `fee == 0`, `safeTransfer(protocolTreasury, 0)` is called. Tokens that revert on zero-value transfers (a documented class of non-standard ERC-20s) cause the entire transaction to revert. Because Solidity reverts are atomic, the state update at lines 134–135 is also rolled back, so the user's claim is never recorded — but every subsequent attempt also reverts for the same reason, permanently blocking the claim.

Note: the submitted report incorrectly states the state is "permanently marked as claimed but no tokens received." In reality the whole transaction reverts atomically, so the state is never written. The actual impact — permanent inability to claim — is still valid.

`KernelMerkleDistributor._processClaim()` (lines 341–343) applies the correct guard:
```solidity
if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);
}
```
`MerkleDistributor` was not updated to match.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Users whose `claimableAmount` produces `fee == 0` (due to `feeInBPS = 0` or integer truncation) cannot claim their tokens when the distributed token reverts on zero-value transfers. The yield is permanently frozen in the contract with no recovery path for the affected users. This matches the allowed impact "Medium. Permanent freezing of unclaimed yield."

The submitted report labels this High, but the allowed scope assigns High only to *theft* of unclaimed yield; permanent *freezing* of unclaimed yield is Medium.

## Likelihood Explanation
- `feeInBPS = 0` is explicitly allowed and can be set by the owner at any time via `setFeeInBPS`.
- Integer truncation to zero occurs for any `claimableAmount < 10_000 / feeInBPS` (e.g., any amount below 10,000 tokens when `feeInBPS = 1`).
- `MerkleDistributor` is a generic distributor deployable with any ERC-20 token, including tokens with non-standard zero-transfer behavior.
- The exploit path is fully unprivileged: any user calls `claim()` with a valid merkle proof.

## Recommendation
Add a zero-check before the fee transfer, mirroring the pattern in `KernelMerkleDistributor._processClaim()`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

## Proof of Concept
1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers and `feeInBPS = 0`.
2. Set a merkle root with a valid leaf for a user with `cumulativeAmount = 100`.
3. User calls `claim(index, account, 100, proof)`.
4. `fee = (100 * 0) / 10_000 = 0`.
5. `safeTransfer(account, 100)` succeeds.
6. `safeTransfer(protocolTreasury, 0)` reverts → entire transaction reverts.
7. User can never claim; every retry hits the same revert. Yield is permanently frozen.

Alternatively: `feeInBPS = 1`, `claimableAmount = 50` → `fee = 0` → same revert path. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
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
