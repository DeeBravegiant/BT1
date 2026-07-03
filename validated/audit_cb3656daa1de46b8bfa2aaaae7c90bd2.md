Audit Report

## Title
Blacklisted `protocolTreasury` or `account` Freezes Unclaimed Yield in `MerkleDistributor.claim` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim` performs two sequential `safeTransfer` calls in a single atomic transaction — one to the claimant and one to `protocolTreasury` for the fee. If the distributed token is a blacklistable ERC20 (e.g., USDC) and `protocolTreasury` is blacklisted, every user's `claim` reverts, freezing all unclaimed yield. A secondary variant exists where a blacklisted `account` permanently loses access to their individual allocation with no recovery path.

## Finding Description
In `claim` (lines 141–144), both transfers execute atomically with no error isolation:

```solidity
IERC20(token).safeTransfer(account, amountToSend);       // L141
IERC20(token).safeTransfer(protocolTreasury, fee);       // L144
```

The `token` address is owner-configurable via `setToken` (lines 185–193), meaning USDC or any blacklistable ERC20 can be set. USDC's blacklist check fires on any transfer — including zero-value — to or from a blacklisted address. If `protocolTreasury` is blacklisted, line 144 reverts unconditionally. Because there is no try/catch and both transfers share the same transaction, the state updates at lines 134–135 are also rolled back, leaving the claim reusable but permanently unexecutable. Every subsequent call by any user hits the same revert. The owner can remediate by calling `setProtocolTreasury` (lines 172–180), making this a temporary-to-indefinite freeze depending on owner responsiveness. For the per-user variant (blacklisted `account`), line 141 reverts and there is no admin function to redirect that user's allocation to an alternative address — the freeze is permanent.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield (per-user variant) / Temporary freezing of funds (protocol-treasury variant).**

- `protocolTreasury` blacklisted: 100% of pending claimants are blocked simultaneously; all yield held in the contract is frozen until the owner calls `setProtocolTreasury`. This matches "Temporary freezing of funds" (Medium).
- `account` blacklisted: that user's entire unclaimed allocation is permanently inaccessible with no on-chain recovery path. This matches "Permanent freezing of unclaimed yield" (Medium).

Both impacts are within the allowed scope.

## Likelihood Explanation
USDC blacklisting of protocol-controlled multisigs has occurred historically (e.g., Tornado Cash-linked addresses). The `token` field is explicitly designed to accept any ERC20, including USDC. The `protocolTreasury` is a single point of failure: one blacklisting event simultaneously blocks every pending claimant. Individual user blacklisting is a realistic scenario for any user interacting with regulated stablecoins. No special attacker capability is required — the blacklisting is performed by the token issuer, and the revert is triggered by any ordinary `claim` call thereafter.

## Recommendation
1. **Accumulate fees internally** rather than pushing them on every claim. Add a separate pull-based `withdrawFees()` function:
```solidity
accruedFees += fee;
IERC20(token).safeTransfer(account, amountToSend);

function withdrawFees(address recipient) external onlyOwner {
    uint256 amount = accruedFees;
    accruedFees = 0;
    IERC20(token).safeTransfer(recipient, amount);
}
```
2. **Guard the fee transfer**: if `fee == 0` or the transfer would fail, still deliver the user's portion. At minimum, skip the treasury transfer when `fee == 0`.
3. For the per-user variant, consider allowing the claimant to specify an alternative `recipient` address, or provide an admin rescue function to redirect a blacklisted user's allocation.

## Proof of Concept
1. Deploy `MerkleDistributor` with `token = USDC`, `protocolTreasury = 0xTreasury`, `feeInBPS = 500`.
2. Owner posts a merkle root; multiple users are eligible.
3. USDC issuer blacklists `0xTreasury`.
4. Any user calls `claim(index, account, cumulativeAmount, proof)`.
5. Execution reaches line 144: `IERC20(token).safeTransfer(protocolTreasury, fee)` → USDC reverts (blacklist check on recipient).
6. Entire transaction reverts; state updates at lines 134–135 are rolled back.
7. All subsequent `claim` calls by all users revert identically.

**Foundry fork test plan:**
```solidity
// Fork mainnet, impersonate USDC blacklister, blacklist protocolTreasury
// Call distributor.claim(...) from a valid claimant
// Assert: transaction reverts with USDC transfer failure
// Assert: userClaims[account].cumulativeAmount unchanged
// Assert: all other users' claim calls also revert
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L133-135)
```text
        // Update user claim info, and send the token.
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L141-144)
```text
        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L172-180)
```text
    function setProtocolTreasury(address _protocolTreasury) external onlyOwner {
        if (_protocolTreasury == address(0)) {
            revert ZeroValueProvided();
        }

        protocolTreasury = _protocolTreasury;

        emit ProtocolTreasuryUpdated(_protocolTreasury);
    }
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
