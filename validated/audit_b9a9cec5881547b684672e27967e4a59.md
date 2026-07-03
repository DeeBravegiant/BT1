### Title
Duplicate `(index, account)` Merkle Leaf Entries Cause Permanent Freezing of KERNEL Reward Funds — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`, `contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`MerkleDistributor` and `KernelMerkleDistributor` encode a `(index, account, cumulativeAmount)` triple as the Merkle leaf and track claims via `userClaims[account].lastClaimedIndex >= index`. If the same `(index, account)` pair appears in the Merkle tree with two different `cumulativeAmount` values — a scenario that cannot be validated on-chain — one leaf becomes permanently unclaimable after the other is claimed, and the KERNEL tokens allocated to it are frozen in the contract with no rescue path.

---

### Finding Description

Both `MerkleDistributor` and `KernelMerkleDistributor` construct the leaf as:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) [2](#0-1) 

Claim status is tracked per-account using:

```solidity
return userClaims[account].lastClaimedIndex >= index;
``` [3](#0-2) [4](#0-3) 

After a successful claim, the state is updated as:

```solidity
userClaims[account].lastClaimedIndex = index;
userClaims[account].cumulativeAmount = cumulativeAmount;
``` [5](#0-4) [6](#0-5) 

There is no on-chain enforcement that a given `(index, account)` pair is unique across all leaves. If the off-chain Merkle tree generator emits two leaves for the same account at the same root version index — e.g., `(1, alice, 100)` and `(1, alice, 200)` — the following occurs:

1. Alice claims leaf `(1, alice, 100)` → `lastClaimedIndex = 1`, `cumulativeAmount = 100`.
2. Alice attempts to claim leaf `(1, alice, 200)` → `isClaimed(1, alice)` returns `true` → reverts with `AlreadyClaimed`.

The 100 KERNEL tokens allocated to the second leaf are permanently frozen. Neither `MerkleDistributor` nor `KernelMerkleDistributor` exposes any `withdrawTokens` or emergency-rescue function, unlike `KernelTop100MerkleDistributor` which does have one. [7](#0-6) 

---

### Impact Explanation

**Permanent freezing of unclaimed yield.** KERNEL tokens deposited into `KernelMerkleDistributor` (or any ERC-20 in `MerkleDistributor`) that correspond to a duplicate-keyed leaf are irrecoverable. There is no owner sweep, no expiry, and no fallback withdrawal path in either contract.

---

### Likelihood Explanation

Low-to-medium. The Merkle tree is generated off-chain by the protocol backend. A software bug, data-pipeline race condition, or accidental double-insertion of a recipient at the same root version index would silently produce a malformed tree. The contract accepts any root the owner sets with no structural validation. The Sablier audit acknowledged the identical class of issue under the same trust assumption.

---

### Recommendation

1. **Preferred**: Remove `index` from the leaf encoding and instead derive uniqueness from the Merkle proof path itself (as shown in the Eigenlayer `processInclusionProofKeccak` example in the external report). This makes duplicate-index leaves structurally impossible.
2. **Minimum**: Add an owner-callable `withdrawTokens` rescue function to both `MerkleDistributor` and `KernelMerkleDistributor`, mirroring the one already present in `KernelTop100MerkleDistributor`, so that funds stranded by a malformed tree can be recovered.

---

### Proof of Concept

**Setup**: Owner calls `setMerkleRoot` with a root built from a tree containing two leaves for the same account at the same index:
- Leaf A: `keccak256(abi.encodePacked(uint256(1), alice, uint256(100)))`
- Leaf B: `keccak256(abi.encodePacked(uint256(1), alice, uint256(200)))`

**Step 1** — Alice claims Leaf A:
```solidity
kernelMerkleDistributor.claim(1, alice, 100, proofA);
// userClaims[alice].lastClaimedIndex = 1
// userClaims[alice].cumulativeAmount = 100
```

**Step 2** — Alice attempts to claim Leaf B:
```solidity
kernelMerkleDistributor.claim(1, alice, 200, proofB);
// isClaimed(1, alice) → lastClaimedIndex(1) >= index(1) → true
// → reverts: AlreadyClaimed
```

The 100 KERNEL tokens (the incremental allocation of Leaf B over Leaf A) are permanently locked in `KernelMerkleDistributor` with no recovery path. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L93-93)
```text
        return userClaims[account].lastClaimedIndex >= index;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L111-117)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L120-120)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L134-135)
```text
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L242-242)
```text
        return userClaims[account].lastClaimedIndex >= index;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L307-317)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L320-320)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L334-335)
```text
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-469)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);
```
