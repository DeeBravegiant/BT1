### Title
Atomic fee-and-reward delivery in `_processClaim` can permanently freeze all user claims - (File: `contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

The `_processClaim` function in `KernelMerkleDistributor.sol` atomically transfers the protocol fee to `protocolTreasury` as part of every user claim. If `protocolTreasury` is or becomes a contract that reverts on ERC20 receipt, every call to `claim()` and `claimAndStake()` will revert, permanently freezing all users' unclaimed KERNEL yield. The identical pattern exists in `KernelTop100MerkleDistributor.sol` and `MerkleDistributor.sol`.

---

### Finding Description

In `_processClaim()`, the fee is transferred to `protocolTreasury` via `kernel.safeTransfer(protocolTreasury, fee)` before the function returns the user's claimable amount to the caller:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol lines 337-343
uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
uint256 amountToSend = claimableAmount - fee;

if (fee > 0) {
    kernel.safeTransfer(protocolTreasury, fee);   // @audit atomic with user claim
}

return amountToSend;
```

The caller (`claim()` or `claimAndStake()`) then delivers the user's portion:

```solidity
// lines 261-263
uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);
kernel.safeTransfer(account, amountToSend);
```

Both transfers execute in the same transaction. There is no mechanism to skip the fee transfer or deliver it independently. If `kernel.safeTransfer(protocolTreasury, fee)` reverts for any reason, the entire transaction reverts — including the user's transfer — and the user's claim state is rolled back, leaving them unable to claim.

The same atomic pattern appears in:

- `KernelTop100MerkleDistributor.sol` lines 332–335: fee transferred to `protocolTreasury` before `kernel.safeTransfer(user, amountToSend)`
- `MerkleDistributor.sol` lines 141–144: `IERC20(token).safeTransfer(account, amountToSend)` followed immediately by `IERC20(token).safeTransfer(protocolTreasury, fee)` — here the user transfer executes first but the fee transfer failure still reverts the whole transaction

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

If `protocolTreasury` is a contract that reverts on ERC20 receipt (e.g., a contract lacking ERC20 handling, a paused/bricked treasury, or an address blacklisted by the distributed token), every single user's `claim()` and `claimAndStake()` call will revert. No user can receive any KERNEL yield until the treasury address is replaced by the owner. Because the user's claim state is rolled back on revert, the tokens are not lost — but they are inaccessible for as long as the treasury is non-functional, constituting a permanent freeze of unclaimed yield across the entire distributor.

---

### Likelihood Explanation

`protocolTreasury` is a configurable address set by the owner. If it is pointed at a smart contract treasury (multisig, DAO vault, etc.) that later becomes non-functional — through an upgrade that removes ERC20 receive support, a self-destruct, or a token-level blacklist applied to that address — the freeze activates immediately and affects every claimant. The `MerkleDistributor.sol` variant accepts a generic `token`, making it additionally susceptible to tokens with built-in transfer restrictions (e.g., USDC, USDT). No unprivileged user action is required to trigger the freeze once the treasury address is problematic; any reward claimant's ordinary `claim()` call will hit the revert.

---

### Recommendation

Decouple the fee transfer from the user transfer. Accumulate the fee in a contract-side balance variable and expose a separate `withdrawFees()` function callable by the treasury, following a pull-payment pattern. This ensures that a non-functional treasury address cannot block user claims, mirroring the fix applied in the referenced OrderBook PR 359 (parametrize delivery to a single recipient per call).

---

### Proof of Concept

1. Owner deploys `KernelMerkleDistributor` with `protocolTreasury = address(brokenTreasury)` where `brokenTreasury` is a contract that reverts on any ERC20 `transfer` call (or the KERNEL token issuer blacklists the treasury address).
2. Merkle root is set; users accumulate claimable KERNEL.
3. User calls `claim(index, account, cumulativeAmount, merkleProof)`.
4. `_processClaim` validates the proof, updates `userClaims[account]`, computes `fee`, and calls `kernel.safeTransfer(protocolTreasury, fee)`.
5. `brokenTreasury` reverts → entire transaction reverts → `userClaims[account]` state is rolled back.
6. User retries; same revert occurs. All users across the distributor are permanently blocked from claiming their KERNEL yield until the owner updates `protocolTreasury`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L261-263)
```text
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L337-343)
```text
        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L332-335)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
        kernel.safeTransfer(user, amountToSend);
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L141-144)
```text
        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```
