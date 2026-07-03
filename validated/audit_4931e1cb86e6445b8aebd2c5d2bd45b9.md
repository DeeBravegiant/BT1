### Title
Push-payment `claim()` bundles two USDC transfers; blacklisting of `protocolTreasury` permanently freezes all users' unclaimed yield — (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` uses a push-payment pattern that bundles two `safeTransfer` calls in a single transaction: one to the claiming `account` and one to `protocolTreasury`. If the distributed `token` is USDC (which has a blacklist) and `protocolTreasury` is blacklisted by USDC, every call to `claim()` reverts — permanently freezing unclaimed yield for **all** users of the distributor. The same root cause also freezes an individual user's yield if that user's own address is blacklisted.

---

### Finding Description

`MerkleDistributor` is a generic Merkle-based reward distributor whose `token` is admin-configurable and can be set to USDC. [1](#0-0) 

The `claim()` function, after verifying the Merkle proof and updating state, performs two sequential `safeTransfer` calls with no try/catch or pull-pattern fallback: [2](#0-1) 

USDC's blacklist causes any transfer to or from a blacklisted address to revert. Because both transfers are in the same atomic transaction, a revert on either one rolls back the entire call — including the state update at lines 134–135 — leaving the user's claim permanently uncollectable while the blacklisting persists.

Two concrete trigger paths exist:

1. **`protocolTreasury` is blacklisted** — the fee transfer at line 144 reverts for every caller, blocking the entire user base from claiming.
2. **`account` is blacklisted** — the user transfer at line 141 reverts, permanently freezing that individual user's allocated yield.

The same structural issue exists in `KernelDepositPool.getReward()`, which pushes `rewardsToken` directly to `msg.sender` with no alternative withdrawal path: [3](#0-2) 

If `rewardsToken` is USDC and the staker is blacklisted, `getReward()` always reverts. Unlike the original report's scenario, `initiateWithdrawal` and `claimWithdrawal` do not force a reward transfer (the `updateReward` modifier only updates accounting), so the staked principal is recoverable — but the accumulated reward balance in `rewards[msg.sender]` is permanently inaccessible.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

- In the `MerkleDistributor` treasury-blacklist scenario, 100% of users lose access to their allocated rewards with no recovery path inside the contract.
- In the individual-user scenario (both contracts), the affected user's entire accrued reward balance is permanently locked in the contract.

---

### Likelihood Explanation

**Low.** USDC blacklisting is an infrequent, targeted action by Circle. However, protocol treasury addresses are high-value targets (e.g., sanctioned entities, compliance actions), and the `token` slot is explicitly designed to accept any ERC-20 including USDC. The combination is realistic enough to warrant a fix.

---

### Recommendation

Replace the push-payment pattern with a pull pattern:

1. **`MerkleDistributor`**: Accumulate the fee into an internal `pendingFee` balance instead of transferring it inline. Add a separate `collectFee()` function callable by the owner to sweep accrued fees to `protocolTreasury`. This decouples the user's claim from the treasury transfer entirely.

2. **`KernelDepositPool`**: The existing design already separates reward claiming (`getReward`) from principal withdrawal (`initiateWithdrawal` / `claimWithdrawal`), which is good. No change is strictly required for principal safety, but consider allowing a designated recipient address in `getReward(address _recipient)` so a blacklisted staker can redirect rewards to an unblacklisted address.

---

### Proof of Concept

**Scenario — `protocolTreasury` blacklisted, all users frozen:**

1. Admin deploys `MerkleDistributor` with `token = USDC` and `feeInBPS = 100` (1%).
2. Admin sets a Merkle root; users accumulate claimable USDC allocations.
3. Circle blacklists `protocolTreasury` (e.g., due to a sanctions action).
4. Any user calls `claim(index, account, cumulativeAmount, merkleProof)`.
5. Execution reaches line 141: `IERC20(token).safeTransfer(account, amountToSend)` — succeeds.
6. Execution reaches line 144: `IERC20(token).safeTransfer(protocolTreasury, fee)` — **reverts** (USDC blacklist).
7. Entire transaction reverts; state update at lines 134–135 is rolled back.
8. No user can ever claim while `protocolTreasury` remains blacklisted. All merkle-allocated USDC is permanently frozen in the contract. [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L49-51)
```text
    address public override token;
    address public protocolTreasury;
    uint256 public feeInBPS;
```

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L382-390)
```text
    function getReward() external nonReentrant updateReward(msg.sender) {
        uint256 rewardAmount = rewards[msg.sender];

        if (rewardAmount > 0) {
            rewards[msg.sender] = 0;
            rewardsToken.safeTransfer(msg.sender, rewardAmount);
            emit RewardsClaimed(msg.sender, rewardAmount);
        }
    }
```
