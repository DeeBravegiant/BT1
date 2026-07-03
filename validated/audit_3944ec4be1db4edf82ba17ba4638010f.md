### Title
Unrestricted `claim` Caller Allows Anyone to Force-Claim on Behalf of Any User, Permanently Extracting Protocol Fee From Their Allocation - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

The `claim` function in `MerkleDistributor.sol` accepts an `account` parameter but never verifies that `msg.sender == account`. Because merkle proof data is published publicly (off-chain distribution trees), any unprivileged caller can supply a valid `(index, account, cumulativeAmount, merkleProof)` tuple for any user and trigger their claim. When `feeInBPS > 0`, this forcibly extracts the protocol fee from the victim's allocation and permanently marks the claim as consumed, preventing the user from ever claiming at a lower fee rate.

---

### Finding Description

`MerkleDistributor.sol` `claim` performs three checks before executing:

1. Merkle root is set
2. Index is valid
3. Claim has not already been used for `account`

It then verifies the merkle proof against `keccak256(abi.encodePacked(index, account, cumulativeAmount))` and transfers `claimableAmount - fee` to `account` and `fee` to `protocolTreasury`. [1](#0-0) 

There is no check that `msg.sender == account`. The function is callable by anyone with the public merkle proof data.

Compare this to `KernelMerkleDistributor.sol`, which explicitly guards against this in `_processClaim`: [2](#0-1) 

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

`MerkleDistributor.sol` has no equivalent guard.

After a successful call, the claim state is permanently updated: [3](#0-2) 

This means the victim can never re-claim for the same `index`. The fee deduction is irreversible: [4](#0-3) 

The maximum fee is 10% (1000 BPS): [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When `feeInBPS > 0`, an attacker who force-claims for a victim causes up to 10% of the victim's entire claimable allocation to be permanently redirected to `protocolTreasury` instead of the victim. The victim's `userClaims[account].lastClaimedIndex` is updated to `index`, so any subsequent attempt to claim the same index reverts with `AlreadyClaimed`. The victim permanently loses the fee portion of their yield with no recourse.

---

### Likelihood Explanation

**High.** Merkle distribution trees are always published publicly so that users can construct their own proofs. Any on-chain observer or off-chain reader of the distribution data can immediately construct a valid call. No special privilege, leaked key, or oracle compromise is required. The attacker needs only to read the public distribution data and submit a transaction. The attack is profitable whenever `feeInBPS > 0` and the victim has unclaimed tokens.

---

### Recommendation

Add a caller identity check identical to the one already present in `KernelMerkleDistributor.sol`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Insert this check at the top of the `claim` function body in `MerkleDistributor.sol`, before any state reads or writes. [6](#0-5) 

---

### Proof of Concept

**Setup:**
- `MerkleDistributor` is deployed with `feeInBPS = 500` (5%).
- A merkle root is set. Alice has a valid leaf: `(index=1, account=Alice, cumulativeAmount=1000e18)`.
- The distribution tree is published publicly (standard practice).

**Attack:**
1. Attacker reads the public distribution tree and obtains Alice's `(index, account, cumulativeAmount, merkleProof)`.
2. Attacker calls `MerkleDistributor.claim(1, Alice, 1000e18, aliceProof)` directly.
3. The function passes all checks (valid root, valid index, not yet claimed, valid proof).
4. `claimableAmount = 1000e18`, `fee = 50e18`, `amountToSend = 950e18`.
5. `950e18` tokens are sent to Alice; `50e18` tokens are sent to `protocolTreasury`.
6. `userClaims[Alice].lastClaimedIndex = 1` is written.
7. Alice later attempts to call `claim(1, Alice, 1000e18, aliceProof)` herself — it reverts with `AlreadyClaimed`.
8. Alice permanently loses `50e18` tokens (5% of her allocation) that she would have retained had she been able to claim at a time when `feeInBPS = 0`. [7](#0-6)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
