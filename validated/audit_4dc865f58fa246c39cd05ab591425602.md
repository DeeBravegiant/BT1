### Title
Owner Can Apply Increased Claim Fee Retroactively to Already-Vested Tokens - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

### Summary
`KernelTop100MerkleDistributor` allows the owner to change `feeInBPS` at any time with no timelock or delay. Because the fee is applied at claim time to the entire unclaimed vested balance — including tokens that vested during a prior lower-fee period — the owner can retroactively extract a higher fee from users' already-vested yield.

### Finding Description
`setFeeInBPS()` updates `feeInBPS` immediately with no delay or snapshot mechanism:

```solidity
// contracts/KERNEL/KernelTop100MerkleDistributor.sol L426-432
function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
    if (_feeInBPS > MAX_FEE_IN_BPS) {
        revert InvalidFeeInBPS();
    }
    feeInBPS = _feeInBPS;
    emit FeeInBPSUpdated(feeInBPS);
}
```

When a user calls `claim()`, the fee is computed against the full unclaimed vested amount using the **current** `feeInBPS`:

```solidity
// L317-328
uint256 claimableAmount = _getUnclaimedVestedAmount(user, amount);
...
uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
```

`_getUnclaimedVestedAmount()` returns all tokens vested since the last claim (or since `vestingStartTimestamp`), with no record of what fee rate was active during each sub-period:

```solidity
// L261-270
uint256 totalElapsedTime = currentTime - vestingStartTimestamp;
uint256 totalVestedAmount = (userTotalClaimableAmount * totalElapsedTime) / VESTING_DURATION;
...
uint256 unclaimedAmount = totalVestedAmount - userClaim.amountClaimed;
```

There is no per-period fee snapshot, no checkpoint on fee change, and no timelock. The new `feeInBPS` is applied uniformly to the entire unclaimed balance, including the portion that vested when the fee was lower (or zero).

### Impact Explanation
**High — Theft of unclaimed yield.**

Users lose a portion of tokens that vested under a lower fee rate. The owner can set `feeInBPS` from 0 to the maximum of 1000 bps (10%) at any moment. For a user with 10,000 KERNEL allocated over 30 days who has not yet claimed after 15 days (5,000 KERNEL vested at 0% fee), a fee increase to 10% causes 500 KERNEL to be diverted to the treasury — tokens that should have been delivered fee-free. This is a direct, quantifiable loss of user yield with no recourse.

### Likelihood Explanation
The owner can trigger this at any time with a single transaction. The vesting window is 30 days (`VESTING_DURATION`), giving a sustained opportunity. Users cannot predict or front-run a fee change, and there is no on-chain mechanism (timelock, grace period, or fee-change event with a delay) to allow users to claim before the new fee takes effect. Any user who does not claim immediately after every block is exposed.

### Recommendation
1. **Snapshot the fee at vesting start or at each claim**, storing the fee rate that applied to each vested sub-period, and apply it only to tokens that vested during that period.
2. Alternatively, **enforce a timelock** (e.g., a two-step propose/apply pattern with a mandatory delay) on `setFeeInBPS`, giving users time to claim under the old rate before the new one takes effect.
3. At minimum, **emit a fee-change event with a future effective timestamp** and enforce that the new fee only applies to tokens vesting after that timestamp.

### Proof of Concept
1. Contract is deployed with `feeInBPS = 0`.
2. Vesting starts (`vestingStartTimestamp = T`). User U is entitled to 10,000 KERNEL over 30 days.
3. At `T + 15 days`, 5,000 KERNEL have vested for U. U has not yet claimed.
4. Owner calls `setFeeInBPS(1000)` (10%).
5. U calls `claim()`. `_getUnclaimedVestedAmount()` returns 5,000 KERNEL.
6. Fee = `5000 * 1000 / 10000 = 500 KERNEL` is sent to `protocolTreasury`.
7. U receives only 4,500 KERNEL, despite all 5,000 having vested during a 0% fee period.
8. The 500 KERNEL loss is a direct theft of unclaimed yield from U. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L247-247)
```text
        uint256 startTime = userClaim.lastClaimTimestamp > 0 ? userClaim.lastClaimTimestamp : vestingStartTimestamp;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L260-270)
```text
        // Calculate total vested amount based on time elapsed since vesting start
        uint256 totalElapsedTime = currentTime - vestingStartTimestamp;
        uint256 totalVestedAmount = (userTotalClaimableAmount * totalElapsedTime) / VESTING_DURATION;

        // Cap at total amount
        if (totalVestedAmount > userTotalClaimableAmount) {
            totalVestedAmount = userTotalClaimableAmount;
        }

        // Calculate unclaimed amount
        uint256 unclaimedAmount = totalVestedAmount - userClaim.amountClaimed;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L327-329)
```text
        // Calculate fee
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;
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
