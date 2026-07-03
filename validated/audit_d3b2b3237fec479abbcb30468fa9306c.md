Audit Report

## Title
Push-payment `claim()` bundles two USDC transfers; blacklisting of `protocolTreasury` temporarily freezes all users' unclaimed yield, and blacklisting of an individual `account` permanently freezes that user's unclaimed yield — (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary

`MerkleDistributor.claim()` performs two sequential `safeTransfer` calls in one atomic transaction: one to `account` and one to `protocolTreasury`. If `token` is USDC and either recipient is USDC-blacklisted, the second transfer reverts and rolls back the entire transaction — including the state update — leaving the claim permanently or temporarily uncollectable. When `protocolTreasury` is blacklisted, every user's claim is blocked until the admin updates the treasury address (temporary freeze). When an individual `account` is blacklisted, that user's yield is permanently inaccessible with no recovery path inside the contract.

## Finding Description

`token` is admin-configurable and explicitly designed to accept any ERC-20, including USDC. In `claim()`, state is written first (lines 134–135), then two `safeTransfer` calls are made with no try/catch or pull-pattern fallback:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol L134-144
userClaims[account].lastClaimedIndex = index;
userClaims[account].cumulativeAmount = cumulativeAmount;

uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);          // L141
IERC20(token).safeTransfer(protocolTreasury, fee);          // L144
```

USDC's blacklist causes any `transfer` call — including zero-amount calls — to or from a blacklisted address to revert. Because both transfers are in the same atomic transaction, a revert on either one rolls back the state writes at lines 134–135, leaving the user's `lastClaimedIndex` and `cumulativeAmount` unchanged. The user can retry, but the same revert will occur on every subsequent attempt while the blacklisting persists.

**Path 1 — `protocolTreasury` blacklisted (all users frozen, temporarily):**
The transfer at L144 reverts for every caller regardless of their own address. The admin can mitigate by calling `setProtocolTreasury(newAddress)`, so the freeze is temporary — but during the window before admin action, 100% of users are blocked from claiming.

**Path 2 — `account` blacklisted (individual user frozen, permanently):**
The transfer at L141 reverts for that specific user. There is no admin function to redirect a user's merkle allocation to a different address, and no pull-pattern fallback. The user's allocated yield is permanently locked in the contract.

Existing guards (`whenNotPaused`, merkle proof verification, `isClaimed`) are all bypassed before the transfers are reached and do not protect against this failure mode.

The same structural issue exists in `KernelDepositPool.getReward()` (L382–390): if `rewardsToken` is USDC and `msg.sender` is blacklisted, `safeTransfer(msg.sender, rewardAmount)` reverts, and since `rewards[msg.sender] = 0` is set before the transfer in the same transaction, the rollback leaves the reward balance intact but permanently inaccessible.

## Impact Explanation

- **Medium — Temporary freezing of funds** (Path 1): All users of the distributor are blocked from claiming their allocated yield for the duration of the `protocolTreasury` blacklisting. The admin can resolve this by updating the treasury address, but no on-chain mechanism forces or guarantees timely remediation.
- **Medium — Permanent freezing of unclaimed yield** (Path 2): An individual blacklisted user has no recovery path. Their merkle-allocated yield and/or staking rewards remain locked in the contract indefinitely.

## Likelihood Explanation

USDC blacklisting is infrequent and targeted. However, `protocolTreasury` is a high-value, publicly known address (e.g., subject to sanctions compliance actions by Circle), and the `token` slot is explicitly designed to accept USDC. The individual-user path requires the claimant's own address to be blacklisted, which is a lower-probability but non-negligible event. Both paths are reachable by any unprivileged external caller through the public `claim()` function with no special preconditions beyond the blacklisting itself.

## Recommendation

1. **`MerkleDistributor`**: Replace the inline fee transfer with a pull pattern. Accumulate fees into an internal `pendingFee` storage variable instead of calling `safeTransfer(protocolTreasury, fee)` inside `claim()`. Add a separate `collectFee()` function callable by the owner to sweep accrued fees to `protocolTreasury`. This fully decouples the user's claim from the treasury transfer.

2. **`KernelDepositPool`**: Add an optional `_recipient` parameter to `getReward(address _recipient)` so a blacklisted staker can redirect accumulated rewards to an unblacklisted address they control.

## Proof of Concept

**Scenario A — `protocolTreasury` blacklisted, all users temporarily frozen:**

1. Admin deploys `MerkleDistributor` with `token = USDC` and `feeInBPS = 100`.
2. Admin sets a Merkle root; users accumulate claimable USDC allocations.
3. Circle blacklists `protocolTreasury`.
4. Any user calls `claim(index, account, cumulativeAmount, merkleProof)`.
5. L141: `safeTransfer(account, amountToSend)` — succeeds (account not blacklisted).
6. L144: `safeTransfer(protocolTreasury, fee)` — **reverts** (USDC blacklist).
7. Entire transaction reverts; state writes at L134–135 are rolled back.
8. All users are blocked from claiming until admin calls `setProtocolTreasury(newAddress)`.

**Scenario B — `account` blacklisted, individual user permanently frozen:**

1. Same setup as above.
2. Circle blacklists a specific user's address.
3. That user calls `claim(...)`.
4. L141: `safeTransfer(account, amountToSend)` — **reverts** (USDC blacklist).
5. Transaction reverts; no state change.
6. No admin function exists to redirect the merkle allocation to a different address.
7. User's yield is permanently locked.

**Foundry fork test outline:**
```solidity
// Fork mainnet, deploy MerkleDistributor with token = USDC
// Set protocolTreasury to a USDC-blacklisted address (e.g., a known sanctioned address)
// Set merkle root, fund contract with USDC
// vm.expectRevert(); distributor.claim(index, alice, amount, proof);
// Assert userClaims[alice].lastClaimedIndex == 0 (state not updated)
```