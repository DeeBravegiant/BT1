Audit Report

## Title
Zero-Amount Fee Transfer in `MerkleDistributor.claim()` Freezes Unclaimed Yield for Revert-on-Zero Tokens - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary

`MerkleDistributor.claim()` unconditionally calls `safeTransfer(protocolTreasury, fee)` without checking whether `fee > 0`. When `feeInBPS` is zero — a valid configuration — this produces a zero-amount transfer. ERC20 tokens that revert on zero-value transfers will cause every `claim()` call to revert, permanently freezing all unclaimed yield for the duration of that configuration.

## Finding Description

In `MerkleDistributor.claim()`, lines 138–144:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

// Send the fee to the protocol treasury
IERC20(token).safeTransfer(protocolTreasury, fee);   // ← no zero-check
``` [1](#0-0) 

When `feeInBPS == 0`, `fee` evaluates to `0` and the unconditional `safeTransfer(protocolTreasury, 0)` executes. Tokens that revert on zero-value transfers will cause the entire `claim()` call to revert.

`feeInBPS` is permitted to be zero: `initialize()` and `setFeeInBPS()` only reject values **above** `MAX_FEE_IN_BPS` (1000), so `feeInBPS = 0` is explicitly allowed. [2](#0-1) [3](#0-2) 

The sibling contracts `KernelMerkleDistributor` and `KernelTop100MerkleDistributor` both guard this transfer correctly:

```solidity
if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);
}
``` [4](#0-3) [5](#0-4) 

`MerkleDistributor` is missing this guard entirely. The `token` address is also owner-configurable via `setToken()` with no restriction on token type, making a revert-on-zero token a realistic deployment choice. [6](#0-5) 

## Impact Explanation

Any user calling `claim()` when `feeInBPS == 0` and the distributed token reverts on zero-value transfers will have their call revert. Because `claim()` is the only mechanism for users to receive their allocated tokens, their unclaimed yield is frozen for as long as this condition holds. If `feeInBPS` remains zero, the freeze is permanent.

**Impact: Medium — Permanent freezing of unclaimed yield.**

## Likelihood Explanation

- `feeInBPS = 0` is a valid, operationally reasonable configuration (no protocol fee taken) and can be set at initialization or via `setFeeInBPS(0)`.
- The `token` address is unrestricted; deploying with a revert-on-zero ERC20 (e.g., LEND, BNB, and others in the weird-erc20 catalogue) is a realistic scenario for a generic distributor.
- No attacker action is required; the freeze affects all claimants automatically once both conditions hold.
- The inconsistency with sibling contracts confirms the guard was intentionally applied elsewhere but omitted here.

## Recommendation

Add a zero-amount guard before the fee transfer, consistent with the pattern in `KernelMerkleDistributor` and `KernelTop100MerkleDistributor`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

## Proof of Concept

1. Deploy a mock ERC20 that reverts on zero-value transfers.
2. Deploy `MerkleDistributor` with that token and `feeInBPS = 0` (or call `setFeeInBPS(0)` post-deployment).
3. Set a valid merkle root and fund the contract.
4. Any user calls `claim(index, account, cumulativeAmount, merkleProof)` with a valid proof.
5. `fee = (claimableAmount * 0) / 10_000 = 0`.
6. `IERC20(token).safeTransfer(protocolTreasury, 0)` reverts because the token disallows zero-value transfers.
7. The entire `claim()` call reverts; the user's allocation is never transferred.

**Foundry test sketch:**
```solidity
function test_claimRevertsOnZeroFeeWithRevertOnZeroToken() public {
    // Deploy revert-on-zero mock token, fund distributor
    // Initialize with feeInBPS = 0
    // Set merkle root, build valid proof for user
    vm.prank(user);
    vm.expectRevert();
    distributor.claim(index, user, cumulativeAmount, proof);
}
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L77-79)
```text
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L332-334)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```
