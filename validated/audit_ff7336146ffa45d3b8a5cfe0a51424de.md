### Title
Mutable `feeInBPS` Applies Retroactively to All Committed Merkle Entitlements, Stealing Unclaimed Yield - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
The `feeInBPS` in `MerkleDistributor`, `KernelMerkleDistributor`, and `KernelTop100MerkleDistributor` is mutable by the owner via `setFeeInBPS()`. The fee is deducted at claim time using the **current** rate, not the rate in effect when the merkle root (which commits to each user's entitled amount) was published. Any fee increase after a merkle root is set silently reduces what every pending claimant receives, constituting theft of unclaimed yield.

### Finding Description
In `MerkleDistributor.sol`, the owner can call `setFeeInBPS()` at any time to update `feeInBPS` up to `MAX_FEE_IN_BPS = 1000` (10%):

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol L198-L206
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) {
        revert InvalidFeeInBPS();
    }
    feeInBPS = _feeInBPS;
    emit FeeInBPSUpdated(_feeInBPS);
}
```

When a user calls `claim()`, the fee is computed against the live `feeInBPS`:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol L138-L144
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;
IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
```

The merkle root encodes a `cumulativeAmount` representing the total tokens a user is entitled to. That commitment is made at `setMerkleRoot()` time. There is no snapshot of `feeInBPS` stored alongside the root, and no mechanism to lock the fee for already-published roots. The identical pattern exists in `KernelMerkleDistributor._processClaim()` (lines 338–342) and `KernelTop100MerkleDistributor.claim()` / `claimAndStake()` (lines 328–329, 363–364).

**Exploit flow:**
1. Owner publishes a merkle root committing user Alice to 1,000 KERNEL with `feeInBPS = 0`.
2. Alice does not claim immediately (e.g., waits for vesting in `KernelTop100MerkleDistributor`, or simply delays).
3. Owner calls `setFeeInBPS(1000)` (10% — the maximum allowed).
4. Alice calls `claim()`. She receives 900 KERNEL instead of 1,000. The 100 KERNEL difference flows to `protocolTreasury`.

No attacker-controlled key is needed beyond the owner performing a routine fee-update operation. The impact is purely on the reward claimant (unprivileged user).

### Impact Explanation
Every user with unclaimed rewards in any published merkle root is retroactively subject to the new fee rate. A fee increase from 0% to 10% causes all pending claimants to lose up to 10% of their committed entitlement. This is a direct, quantifiable theft of unclaimed yield. Severity: **High** (theft of unclaimed yield per the allowed impact scope).

### Likelihood Explanation
The owner is expected to adjust fees over the protocol's lifetime — this is a normal operational action, not a compromise. The `MAX_FEE_IN_BPS = 1000` cap confirms the protocol anticipates non-zero fees. Any fee increase after a merkle root is published triggers the impact for all users who have not yet claimed. Likelihood: **Medium** (routine admin action with broad retroactive effect).

### Recommendation
Snapshot `feeInBPS` at the time each merkle root is published and store it alongside the root. Apply the snapshotted fee rate when processing claims against that root, not the current live rate. Alternatively, make `feeInBPS` immutable after deployment, or require a time-lock before fee increases take effect so users can claim at the old rate before the new rate applies.

### Proof of Concept
1. Deploy `MerkleDistributor` with `feeInBPS = 0`.
2. Call `setMerkleRoot(root)` where `root` commits Alice to `cumulativeAmount = 1000e18`.
3. Call `setFeeInBPS(1000)` (owner, 10%).
4. Alice calls `claim(index, alice, 1000e18, proof)`.
5. Observe: Alice receives `900e18` tokens; `protocolTreasury` receives `100e18` tokens.
6. Alice's committed entitlement was `1000e18`; she received `100e18` less than promised.

Affected files and lines:
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3) 
- [5](#0-4) 
- [6](#0-5)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L337-345)
```text
        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L388-396)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        feeInBPS = _feeInBPS;

        emit FeeInBPSUpdated(feeInBPS);
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L327-335)
```text
        // Calculate fee
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        // Transfer tokens
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
        kernel.safeTransfer(user, amountToSend);
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L426-432)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }
        feeInBPS = _feeInBPS;
        emit FeeInBPSUpdated(feeInBPS);
    }
```
