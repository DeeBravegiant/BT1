### Title
Address-Based Claim Tracking Causes Permanent Yield Freeze When User Has Multiple Merkle Leaves - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

### Summary
`KernelTop100MerkleDistributor` tracks claimed state via a single address-keyed `amountClaimed` counter with no leaf-level deduplication. If the Merkle tree contains two independent leaves for the same user (e.g., two separate reward categories), the shared counter causes the second leaf's claimable amount to be silently reduced or zeroed out, permanently locking the user's entitled tokens in the contract.

### Finding Description
`_verifyClaimProof` constructs the leaf as `keccak256(abi.encodePacked(user, amount))` — containing only the user address and amount, with no index or category identifier. [1](#0-0) 

The claimed state is stored in a single per-address struct: [2](#0-1) 

`_getUnclaimedVestedAmount` computes the claimable amount as `totalVestedAmount - userClaim.amountClaimed`, where `userClaim.amountClaimed` is the running total across **all** prior claims by that address: [3](#0-2) 

After each successful claim, `amountClaimed` is incremented: [4](#0-3) 

There is no leaf-based bitmap or mapping to record which specific leaf has been consumed. The contract never documents whether multiple leaves per address are intended or forbidden.

### Impact Explanation
If the Merkle tree contains two independent leaves for the same user — e.g., `(alice, 100)` for reward category A and `(alice, 200)` for reward category B — the following permanent loss occurs:

**Path 1 — smaller leaf claimed first:**
- `claim(100, proof1)`: `amountClaimed = 100`, Alice receives 100.
- `claim(200, proof2)`: `_getUnclaimedVestedAmount(alice, 200)` returns `200 − 100 = 100`. Alice receives only 100 instead of 200.
- Total received: 200 out of an entitled 300. **100 tokens permanently frozen.**

**Path 2 — larger leaf claimed first:**
- `claim(200, proof2)`: `amountClaimed = 200`, Alice receives 200.
- `claim(100, proof1)`: `amountClaimed (200) >= 100` → returns 0 → reverts `NoTokensToClaim`.
- Total received: 200 out of an entitled 300. **100 tokens permanently frozen.**

The frozen tokens remain in the contract and can only be recovered by the owner via `withdrawTokens`, meaning the user's yield is permanently lost to them. This matches the **Medium — Permanent freezing of unclaimed yield** impact tier. [5](#0-4) 

### Likelihood Explanation
The `KernelTop100MerkleDistributor` is designed for a named "Top 100" cohort. Off-chain Merkle tree builders may legitimately assign multiple independent reward entries to the same address (e.g., one entry per qualifying epoch or reward category). Because the contract provides no on-chain enforcement or documentation of the one-leaf-per-address assumption, a tree builder following a multi-category reward model will silently produce a tree that triggers this freeze for every affected user. No attacker action is required — the user simply calls `claim` twice with their two valid proofs.

### Recommendation
Choose one of the two designs and enforce it explicitly:

1. **One leaf per address (current implicit intent):** Add an `AlreadyClaimed` guard keyed on address (e.g., `if (userClaims[user].amountClaimed > 0 && /* first claim already done */ ) revert`), or document clearly that the Merkle tree must contain at most one leaf per address.

2. **Leaf-based tracking:** Include a unique `index` (or category identifier) in the leaf encoding — `keccak256(abi.encodePacked(index, user, amount))` — and maintain a `mapping(bytes32 leafHash => bool claimed)` or a `mapping(uint256 index => mapping(address => bool))` so each leaf is independently claimable without polluting the other leaf's counter.

The sibling contracts `MerkleDistributor` and `KernelMerkleDistributor` already include an `index` in the leaf and use `lastClaimedIndex` for deduplication; `KernelTop100MerkleDistributor` should be brought to the same standard. [6](#0-5) 

### Proof of Concept
```
Setup:
  Merkle tree leaves:
    Leaf A: keccak256(abi.encodePacked(alice, 100e18))  // category-A reward
    Leaf B: keccak256(abi.encodePacked(alice, 200e18))  // category-B reward

  Both leaves are valid and included in merkleRoot.
  Vesting is complete (block.timestamp >= vestingStartTimestamp + VESTING_DURATION).

Step 1: alice calls claim(100e18, proofA)
  _verifyClaimProof: leaf = keccak256(alice, 100e18) → valid ✓
  _getUnclaimedVestedAmount(alice, 100e18):
    amountClaimed = 0, totalVestedAmount = 100e18
    returns 100e18
  userClaims[alice].amountClaimed = 100e18
  alice receives 100e18 (minus fee)

Step 2: alice calls claim(200e18, proofB)
  _verifyClaimProof: leaf = keccak256(alice, 200e18) → valid ✓
  _getUnclaimedVestedAmount(alice, 200e18):
    amountClaimed = 100e18, totalVestedAmount = 200e18
    unclaimedAmount = 200e18 - 100e18 = 100e18   ← should be 200e18
  userClaims[alice].amountClaimed = 200e18
  alice receives 100e18 (minus fee)   ← 100e18 permanently frozen

Total alice received: 200e18
Total alice entitled: 300e18
Frozen in contract:  100e18  (recoverable only by owner, not alice)
```

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L152-158)
```text
    struct UserClaim {
        uint256 lastClaimTimestamp;
        uint256 amountClaimed;
    }

    /// @notice The user claims mapping
    mapping(address user => UserClaim userClaim) public userClaims;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L239-270)
```text
        if (userClaim.amountClaimed >= userTotalClaimableAmount) {
            return 0;
        }

        // Calculate vesting end time
        uint256 vestingEndTime = vestingStartTimestamp + VESTING_DURATION;

        // Calculate start and end times for the period
        uint256 startTime = userClaim.lastClaimTimestamp > 0 ? userClaim.lastClaimTimestamp : vestingStartTimestamp;

        // Cap current time at vesting end time
        uint256 currentTime = block.timestamp;
        if (currentTime > vestingEndTime) {
            currentTime = vestingEndTime;
        }

        // If current time is before start time or vesting hasn't started yet, nothing to claim
        if (currentTime <= startTime || currentTime <= vestingStartTimestamp) {
            return 0;
        }

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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L292-294)
```text
        // Verify the merkle proof
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L324-325)
```text
        userClaims[user].lastClaimTimestamp = block.timestamp;
        userClaims[user].amountClaimed += claimableAmount;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-470)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);

```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L119-121)
```text
        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
```
