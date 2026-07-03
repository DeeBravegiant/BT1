### Title
Zero-Amount Fee Transfer in `MerkleDistributor.claim()` Freezes Unclaimed Yield for Revert-on-Zero Tokens - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` unconditionally transfers the computed `fee` to `protocolTreasury` without checking whether `fee > 0`. When `feeInBPS` is zero (a valid configuration), this produces a zero-amount `safeTransfer` call. If the distributed token reverts on zero-value transfers (a known class of ERC20 tokens), every user's `claim()` call reverts, permanently freezing their unclaimed yield.

---

### Finding Description

In `MerkleDistributor.claim()`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

// Send the fee to the protocol treasury
IERC20(token).safeTransfer(protocolTreasury, fee);   // ← no zero-check
``` [1](#0-0) 

When `feeInBPS == 0`, `fee` evaluates to `0`, and the unconditional `safeTransfer(protocolTreasury, 0)` is executed. Tokens that revert on zero-value transfers (e.g., LEND, BNB, and others documented at [weird-erc20](https://github.com/d-xo/weird-erc20#revert-on-zero-value-transfers)) will cause the entire `claim()` call to revert.

`feeInBPS` is allowed to be zero: the `initialize()` function only rejects values **above** `MAX_FEE_IN_BPS` (1000), and `setFeeInBPS(0)` is explicitly permitted by the same guard. [2](#0-1) [3](#0-2) 

By contrast, the sibling contracts `KernelMerkleDistributor` and `KernelTop100MerkleDistributor` both correctly guard the fee transfer:

```solidity
if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);
}
``` [4](#0-3) [5](#0-4) 

`MerkleDistributor` is missing this guard entirely.

---

### Impact Explanation

Any user calling `claim()` when `feeInBPS == 0` and the distributed token reverts on zero-value transfers will have their call revert. Because `claim()` is the **only** way for a user to receive their allocated tokens, their unclaimed yield is frozen for as long as this condition holds. If `feeInBPS` is set to zero and never changed, the freeze is permanent.

**Impact: Medium — Permanent freezing of unclaimed yield.**

---

### Likelihood Explanation

- `feeInBPS == 0` is a valid and operationally reasonable configuration (no fee taken). It can be set at initialization or later via `setFeeInBPS(0)`.
- The `token` address is configurable via `setToken()` and is not restricted to any specific token, so a revert-on-zero token is a realistic deployment choice.
- No attacker action is required; the freeze occurs automatically for all claimants under this configuration.

---

### Recommendation

Add a zero-amount guard before the fee transfer, consistent with the pattern already used in `KernelMerkleDistributor` and `KernelTop100MerkleDistributor`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
``` [6](#0-5) 

---

### Proof of Concept

1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers and `feeInBPS = 0` (or call `setFeeInBPS(0)` after deployment).
2. Set a valid merkle root and fund the contract.
3. Any user calls `claim(index, account, cumulativeAmount, merkleProof)` with a valid proof.
4. `fee = (claimableAmount * 0) / 10_000 = 0`.
5. `IERC20(token).safeTransfer(protocolTreasury, 0)` reverts because the token disallows zero-value transfers.
6. The entire `claim()` call reverts. The user's merkle allocation is never transferred. Unclaimed yield is frozen.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L77-79)
```text
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
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
