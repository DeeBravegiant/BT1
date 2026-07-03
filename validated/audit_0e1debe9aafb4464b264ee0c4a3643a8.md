### Title
Blocklist-token incompatibility in `claim()` atomically freezes all user funds via `protocolTreasury` fee push — (`File: contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim()` atomically pushes tokens to both the claimant (`account`) and `protocolTreasury` in a single transaction. If the distributed token implements an admin-controlled address blocklist (e.g. USDC, USDT) and `protocolTreasury` is added to that blocklist, every call to `claim()` will revert, permanently freezing all user funds held in the distributor.

---

### Finding Description

`MerkleDistributor.claim()` performs two sequential `safeTransfer` calls in the same transaction:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol  lines 141-144
IERC20(token).safeTransfer(account, amountToSend);   // user share
IERC20(token).safeTransfer(protocolTreasury, fee);   // fee share
``` [1](#0-0) 

The token address is mutable — it can be set to any ERC20 via `setToken()`:

```solidity
// line 185-192
function setToken(address _token) external onlyOwner {
    ...
    token = _token;
    ...
}
``` [2](#0-1) 

If the token is a blocklist-capable ERC20 (USDC, USDT) and `protocolTreasury` is placed on that blocklist, the `safeTransfer` to `protocolTreasury` reverts. Because both transfers are atomic within `claim()`, no user — regardless of their own blocklist status — can ever successfully claim. The state update (lines 134–135) that marks the claim as processed has already occurred before the revert unwinds, but since the revert rolls back the entire transaction, the user's `cumulativeAmount` is never updated, leaving all funds permanently locked in the contract with no recovery path callable by users. [3](#0-2) 

The same structural pattern exists in `KernelMerkleDistributor._processClaim()`:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol  line 342
kernel.safeTransfer(protocolTreasury, fee);
``` [4](#0-3) 

---

### Impact Explanation

**Permanent freezing of funds — Critical.**

If `protocolTreasury` is blocklisted by the token contract, every invocation of `claim()` reverts at the fee-transfer step. No user can withdraw any portion of their allocation. All tokens held by the distributor become permanently inaccessible to users. There is no admin escape hatch in `MerkleDistributor` that lets users bypass the fee transfer or redirect funds to themselves.

---

### Likelihood Explanation

**Low.** Two independent conditions must hold simultaneously:
1. The token configured in the distributor must implement an admin-controlled blocklist (e.g. USDC, USDT).
2. The token issuer must add `protocolTreasury` to that blocklist (e.g. due to regulatory action, sanctions compliance, or a compromised treasury address).

Both conditions are realistic for a protocol that distributes stablecoin or regulated-token rewards, and the combination has occurred in practice with USDC/USDT.

---

### Recommendation

Separate the fee transfer from the user transfer so that a blocklisted `protocolTreasury` cannot prevent users from claiming their own allocation. Apply the Pull-over-Push pattern for the fee leg: accumulate fees in a storage variable and let the treasury pull them in a dedicated `collectFees()` call. Alternatively, wrap the fee transfer in a `try/catch` and credit uncollected fees to a recoverable mapping.

---

### Proof of Concept

1. Deploy `MerkleDistributor` with `token = USDC` and `protocolTreasury = 0xTREASURY`, `feeInBPS = 100`.
2. Fund the distributor with 1 000 000 USDC and set a valid merkle root covering user `0xUSER` for 1000 USDC.
3. USDC Centre admin calls `USDC.blacklist(0xTREASURY)`.
4. `0xUSER` calls `MerkleDistributor.claim(index, 0xUSER, 1000e6, proof)`.
5. Execution reaches line 141 — `safeTransfer(0xUSER, 990e6)` succeeds.
6. Execution reaches line 144 — `safeTransfer(0xTREASURY, 10e6)` reverts (`Blacklistable: account is blacklisted`).
7. The entire transaction reverts. `0xUSER`'s `cumulativeAmount` is not updated.
8. Every subsequent call to `claim()` by any user hits the same revert. All distributor funds are permanently frozen. [5](#0-4)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-147)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        whenNotPaused
    {
        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L338-345)
```text
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
```
