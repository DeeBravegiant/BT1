Audit Report

## Title
Missing zero-value guard on fee transfer in `MerkleDistributor.claim()` causes DoS for tokens that revert on zero transfers - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary

`MerkleDistributor.claim()` unconditionally calls `IERC20(token).safeTransfer(protocolTreasury, fee)` even when `feeInBPS = 0` causes `fee` to evaluate to zero. ERC20 tokens that revert on zero-value transfers will cause every `claim()` call to revert, permanently freezing all unclaimed yield in the contract. The sibling contract `KernelMerkleDistributor` correctly guards this transfer with `if (fee > 0)`, confirming the omission in `MerkleDistributor` is an oversight.

## Finding Description

In `MerkleDistributor.claim()`, the fee transfer at line 144 is unconditional:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);  // always executed, even when fee == 0
``` [1](#0-0) 

When `feeInBPS = 0`, `fee` is `0` and `safeTransfer(protocolTreasury, 0)` is called. For ERC20 tokens that revert on zero-value transfers, this causes the entire transaction to revert. The `feeInBPS` field has no lower-bound check — both `initialize` and `setFeeInBPS` explicitly permit `0`: [2](#0-1) [3](#0-2) 

The `token` address is also admin-configurable via `setToken`, meaning the contract can be pointed at any ERC20, including those in the zero-value-revert class. [4](#0-3) 

`KernelMerkleDistributor._processClaim()` correctly guards the identical pattern: [5](#0-4) 

`MerkleDistributor` lacks this guard entirely, confirming the omission is an unintentional inconsistency.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

All users with valid merkle proofs are blocked from claiming their token allocations. The tokens remain locked in the contract with no user-accessible recovery path. The only workaround is for the owner to raise `feeInBPS` above zero, which imposes an unintended fee on all claimants. This matches the allowed impact: *Medium. Permanent freezing of unclaimed yield.*

## Likelihood Explanation

`feeInBPS = 0` is a valid and explicitly supported configuration. The `MerkleDistributor` is a generic contract whose `token` is admin-configurable, making it deployable against any ERC20. Tokens that revert on zero-value transfers are a known, non-trivial class (e.g., LEND, certain rebasing/fee-on-transfer tokens). The combination of `feeInBPS = 0` and such a token is a realistic operational state. Once triggered, every subsequent `claim()` call by any user fails identically — the DoS is total and persistent until the owner intervenes.

## Recommendation

Mirror the guard used in `KernelMerkleDistributor`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

## Proof of Concept

1. Deploy a token that reverts on zero-value transfers.
2. Deploy `MerkleDistributor` with that token and `feeInBPS = 0`.
3. Owner calls `setMerkleRoot(root)` encoding a user allocation.
4. User calls `claim(index, account, cumulativeAmount, proof)` with a valid proof.
5. `claimableAmount > 0`; `fee = (claimableAmount * 0) / 10_000 = 0`.
6. `safeTransfer(account, amountToSend)` succeeds.
7. `safeTransfer(protocolTreasury, 0)` reverts — token rejects zero-value transfer.
8. Entire transaction reverts; user receives nothing. All subsequent claims by any user fail identically.

**Foundry test sketch:**
```solidity
function test_claimRevertsOnZeroFeeTransfer() public {
    // Deploy ZeroTransferRevertToken (reverts on transfer(_, 0))
    // Deploy MerkleDistributor with token=ZeroTransferRevertToken, feeInBPS=0
    // Build merkle tree, set root, fund distributor
    // vm.expectRevert();
    // distributor.claim(index, user, amount, proof);
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L185-192)
```text
    function setToken(address _token) external onlyOwner {
        if (_token == address(0)) {
            revert ZeroValueProvided();
        }

        token = _token;

        emit TokenUpdated(_token);
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L199-201)
```text
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
