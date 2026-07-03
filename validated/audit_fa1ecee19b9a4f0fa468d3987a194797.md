### Title
Unconditional Fee Transfer to `protocolTreasury` in `MerkleDistributor.claim()` Freezes All Users' Unclaimed Yield When Treasury Is Blacklisted — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim()` performs two unconditional `safeTransfer` calls — one to `account` and one to `protocolTreasury` — with no zero-amount guard on the fee transfer. When the distributed token is a blacklistable ERC20 (e.g., USDC) and `protocolTreasury` is added to the token's blacklist, every user's `claim()` invocation reverts, freezing all unclaimed yield until the owner updates the treasury address.

---

### Finding Description

In `MerkleDistributor.claim()`, after updating the user's cumulative claim state, the function executes:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);          // line 141

// Send the fee to the protocol treasury
IERC20(token).safeTransfer(protocolTreasury, fee);          // line 144
``` [1](#0-0) 

There is no `if (fee > 0)` guard before the second `safeTransfer`. For blacklistable tokens such as USDC, the `notBlacklisted(to)` modifier fires on every `transfer()` call regardless of the transfer amount — including zero-value transfers. Consequently:

- When `feeInBPS > 0`: `fee > 0` is transferred to `protocolTreasury`; if `protocolTreasury` is blacklisted, the call reverts.
- When `feeInBPS = 0`: `fee = 0` is still passed to `safeTransfer(protocolTreasury, 0)`; USDC's blacklist check still fires and reverts.

Because the state update (`userClaims[account].lastClaimedIndex` and `userClaims[account].cumulativeAmount`) precedes both transfers, a revert on the second transfer rolls back the state update as well, leaving the user's claim permanently re-enterable but permanently failing. [2](#0-1) 

The analogous `KernelTop100MerkleDistributor.claim()` correctly guards the fee transfer with `if (fee > 0)`, demonstrating the fix is known elsewhere in the codebase but was not applied here. [3](#0-2) 

---

### Impact Explanation

**Temporary freezing of unclaimed yield (Medium).** If `protocolTreasury` is blacklisted by the distributed token, every user's `claim()` call reverts. No user can receive their allocated tokens until the contract owner calls `setProtocolTreasury()` to point to a non-blacklisted address. During the window between blacklisting and the owner's remediation, all unclaimed yield is frozen for the entire user base.

---

### Likelihood Explanation

**Low.** Requires two conditions to coincide: (1) the `MerkleDistributor` instance is configured with a blacklistable token such as USDC, and (2) the `protocolTreasury` address is added to that token's blacklist — an action taken by the token issuer, not the protocol. The protocol cannot prevent the token issuer from blacklisting any address. The `MerkleDistributor` is a generic, reusable contract explicitly designed to support arbitrary ERC20 tokens, making USDC a realistic configuration.

---

### Recommendation

1. Add a zero-amount guard before the fee transfer, consistent with `KernelTop100MerkleDistributor`:

```solidity
if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

2. Consider a pull-payment pattern: accumulate fees in a storage variable and expose a separate `withdrawFees()` function callable by the treasury, decoupling fee collection from user claims entirely.

---

### Proof of Concept

1. Deploy `MerkleDistributor` with USDC as `token`, `feeInBPS = 0`, and `protocolTreasury = 0xTREASURY`.
2. USDC's blacklist operator adds `0xTREASURY` to the USDC blacklist.
3. A legitimate user calls `claim(index, account, cumulativeAmount, proof)` with a valid Merkle proof.
4. Execution reaches `IERC20(token).safeTransfer(protocolTreasury, 0)` at line 144.
5. USDC's `transfer()` applies `notBlacklisted(0xTREASURY)`, which reverts.
6. The entire transaction reverts; `userClaims[account]` is unchanged.
7. Every subsequent `claim()` call by any user hits the same revert — all unclaimed yield is frozen until the owner calls `setProtocolTreasury(newAddress)`. [4](#0-3)

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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L169-179)
```text
    /// @dev Set the protocol treasury address.
    /// @dev only called by the owner.
    /// @param _protocolTreasury The address of the protocol treasury.
    function setProtocolTreasury(address _protocolTreasury) external onlyOwner {
        if (_protocolTreasury == address(0)) {
            revert ZeroValueProvided();
        }

        protocolTreasury = _protocolTreasury;

        emit ProtocolTreasuryUpdated(_protocolTreasury);
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L332-336)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
        kernel.safeTransfer(user, amountToSend);

```
