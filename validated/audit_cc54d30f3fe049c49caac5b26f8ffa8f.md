### Title
`setMerkleRoot` Does Not Reset Claim State, Causing Permanent Freeze of Future Rewards When Correcting Overpayments - (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`, `contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`setMerkleRoot()` in `MerkleDistributor`, `KernelMerkleDistributor`, and `MerkleBlastPointsDistributor` replaces the active merkle root without resetting the per-account `userClaims` state. When the owner corrects an overpayment by publishing a new tree with a lower `cumulativeAmount`, any user who already claimed the inflated amount will trigger an arithmetic underflow in the `claimableAmount` subtraction, permanently reverting every future claim attempt for that account across all subsequent epochs.

---

### Finding Description

Each distributor stores per-account claim state in a `UserClaim` struct:

```solidity
struct UserClaim {
    uint256 lastClaimedIndex;
    uint256 cumulativeAmount;   // highest cumulative amount ever claimed
}
```

The `claim()` function derives the incremental payout as:

```solidity
uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

This subtraction is safe only when the new tree's `cumulativeAmount` is monotonically non-decreasing relative to what the account has already claimed. The contract enforces no such invariant.

`setMerkleRoot()` simply overwrites the root and bumps `currentIndex` by one:

```solidity
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    currentMerkleRoot = _merkleRootToSet;
    currentMerkleRootIndex++;
    currentIndex++;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
```

`userClaims` is never touched. If the owner later publishes a corrected tree in which a user's `cumulativeAmount` is lower than `userClaims[account].cumulativeAmount` (because the previous tree overpaid that user), the subtraction underflows under Solidity 0.8 checked arithmetic and reverts. Because `userClaims[account].cumulativeAmount` can never decrease, every future epoch's claim for that account will also revert until the cumulative allocation in the tree surpasses the already-recorded value — which may never happen if the protocol intends to claw back the overpayment.

The same flaw exists identically in:
- `contracts/utils/MerkleDistributor/MerkleDistributor.sol` (token rewards)
- `contracts/KERNEL/KernelMerkleDistributor.sol` (KERNEL token rewards + staking path)
- `contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol` (Blast Points/Gold)

---

### Impact Explanation

A user who claimed an overpaid `cumulativeAmount` in epoch N has `userClaims[account].cumulativeAmount` permanently set to that inflated value. In every subsequent epoch where the corrected tree assigns a lower (or equal) `cumulativeAmount`, the subtraction `cumulativeAmount - userClaims[account].cumulativeAmount` underflows and reverts. The user is frozen from receiving any further token distributions for as long as the corrected cumulative allocation remains below the previously recorded value. If the protocol's intent is to not increase the allocation beyond the overpaid amount (i.e., to effectively claw back), the freeze is permanent. This constitutes permanent freezing of unclaimed yield.

---

### Likelihood Explanation

Merkle tree construction mistakes are a known operational risk (explicitly acknowledged in the referenced external report and in the Paladin/Spearbit discussion). The protocol already exposes `setMerkleRoot` as the correction mechanism. A single off-by-one or data-pipeline error in reward calculation that causes an overpayment, followed by a corrective root update, is sufficient to trigger this freeze for every affected account. Likelihood is low but non-negligible given the periodic, off-chain nature of tree construction.

---

### Recommendation

1. **Epoch-keyed claim state**: Key `userClaims` by `(account, merkleRootIndex)` rather than `account` alone. Each new root starts with a clean slate, eliminating cross-epoch state contamination.
2. **Monotonicity guard**: Before accepting a new root, or inside `claim()`, verify that the new tree's `cumulativeAmount` for any account is always ≥ the stored `userClaims[account].cumulativeAmount`. Revert or skip rather than underflow.
3. **Pause before update**: Require the contract to be paused before `setMerkleRoot` can be called, giving the operator a safe window to audit the transition and reset any affected state.
4. **Explicit correction path**: Add an admin function to reset individual `userClaims` entries when a corrective root is published, analogous to the "reset all bits" recommendation in the external report.

---

### Proof of Concept

**Setup** — `MerkleDistributor` or `KernelMerkleDistributor`, epoch 1:

1. Owner calls `setMerkleRoot(root1)`. `currentIndex = 1`.
   - Tree leaf for User A: `(index=1, account=A, cumulativeAmount=200)` — mistakenly inflated; correct value is 100.

2. User A calls `claim(1, A, 200, proof1)`.
   - `claimableAmount = 200 - 0 = 200`. User A receives 200 tokens.
   - `userClaims[A] = {lastClaimedIndex: 1, cumulativeAmount: 200}`.

**Correction** — epoch 2:

3. Owner discovers the error and calls `setMerkleRoot(root2)`. `currentIndex = 2`.
   - Corrected tree leaf for User A: `(index=2, account=A, cumulativeAmount=100)`.

4. User A calls `claim(2, A, 100, proof2)`.
   - `isClaimed(2, A)` → `1 >= 2` → `false`. Passes.
   - Merkle proof verifies against `root2`. Passes.
   - `claimableAmount = 100 - 200` → **arithmetic underflow → revert** (Solidity 0.8 checked math).

5. In epoch 3, owner publishes `(index=3, account=A, cumulativeAmount=150)`.
   - `claimableAmount = 150 - 200` → **underflow → revert** again.

User A is frozen from all future claims until the cumulative allocation in the tree exceeds 200, which may never occur if the protocol's intent is to not reward the overpaid account further. The 100 tokens of excess payout are unrecoverable, and all future legitimate rewards for User A are also frozen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L58-63)
```text
    struct UserClaim {
        uint256 lastClaimedIndex;
        uint256 cumulativeAmount;
    }

    mapping(address user => UserClaim userClaim) public userClaims;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L125-126)
```text
        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L156-167)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L325-326)
```text
        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L402-413)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L117-118)
```text
        uint256 claimableBlastPoints = cumulativeBlastPointAmount - userClaims[account].cumulativeBlastPointAmount;
        uint256 claimableBlastGold = cumulativeBlastGoldAmount - userClaims[account].cumulativeBlastGoldAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L140-151)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```
