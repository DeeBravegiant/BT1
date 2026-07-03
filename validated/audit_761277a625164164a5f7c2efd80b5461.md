Audit Report

## Title
Cross-Chain Merkle Proof Replay Enables Theft of Unclaimed Yield - (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`, `contracts/KERNEL/KernelMerkleDistributor.sol`, `contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

## Summary
All three merkle distributor contracts compute leaf hashes without binding them to `block.chainid` or the contract address. If the same merkle root is set on distributors deployed across multiple chains — a natural operational pattern for a multi-chain protocol — any user with a valid claim on one chain can replay the identical proof on every other chain and receive tokens from each. The `isClaimed` guard is chain-local and provides no cross-chain protection.

## Finding Description
**Root cause:** Leaf hashes omit chain-binding data.

`MerkleDistributor.sol` L120 and `KernelMerkleDistributor.sol` L320 both compute:
```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) [2](#0-1) 

`KernelTop100MerkleDistributor.sol` L293 computes:
```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [3](#0-2) 

Neither hash includes `block.chainid` or `address(this)`. A proof valid on chain A is mathematically identical on chain B when the same root is set.

**Why existing guards fail:** The `isClaimed` check in `MerkleDistributor.sol` and `KernelMerkleDistributor.sol` reads from the local `userClaims` mapping, which is chain-scoped storage. A claim recorded on Ethereum mainnet does not affect the `userClaims` mapping on Arbitrum. [4](#0-3) [5](#0-4) 

**Exploit path:**
1. Owner calls `setMerkleRoot(root)` on both the Ethereum mainnet and Arbitrum deployments of `KernelMerkleDistributor` with the same `root`.
2. Alice has a valid leaf: `(index=5, account=Alice, cumulativeAmount=1000e18)`.
3. Alice calls `claim(5, Alice, 1000e18, proof)` on mainnet → `userClaims[Alice]` updated on mainnet, 1000 KERNEL transferred.
4. Alice calls `claim(5, Alice, 1000e18, proof)` on Arbitrum with identical arguments → `isClaimed` returns `false` (Arbitrum storage is empty), proof verifies against the same root, 1000 KERNEL transferred again.
5. Steps 3–4 are repeatable on every additional chain where the same root is set. [6](#0-5) 

## Impact Explanation
**High — Theft of unclaimed yield.** Each replayed claim drains tokens from the distributor on the replayed chain that were allocated to other beneficiaries. The stolen asset is KERNEL (or other reward tokens) held in the distributor contracts. This maps exactly to the allowed impact "High. Theft of unclaimed yield." [7](#0-6) 

## Likelihood Explanation
**Medium.** The precondition — same merkle root on multiple chains — is a standard operational pattern for multi-chain reward distributions (one root computed off-chain, pushed to all chain deployments). No admin key compromise is required; the admin simply performs the intended deployment procedure. Any user with a valid claim can exploit this immediately and repeatedly. The protocol already operates across Ethereum mainnet, Arbitrum, Optimism, Scroll, and Linea, making multi-chain distributor deployment a realistic near-term scenario. [8](#0-7) 

## Recommendation
Include `block.chainid` and `address(this)` in every leaf hash to bind proofs to a specific chain and contract instance:

```solidity
// MerkleDistributor / KernelMerkleDistributor
bytes32 node = keccak256(
    abi.encodePacked(block.chainid, address(this), index, account, cumulativeAmount)
);

// KernelTop100MerkleDistributor
bytes32 leaf = keccak256(
    abi.encodePacked(block.chainid, address(this), user, amount)
);
```

The off-chain merkle tree generation must be updated to include the same fields. Alternatively, generate a distinct root per chain (with chain ID embedded in each leaf off-chain) and enforce on-chain that the root was intended for the current chain. [9](#0-8) [10](#0-9) 

## Proof of Concept
**Foundry fork test plan:**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "../contracts/KERNEL/KernelMerkleDistributor.sol";

contract CrossChainReplayTest is Test {
    // 1. Deploy KernelMerkleDistributor on a simulated "mainnet" fork (chainid=1)
    // 2. Deploy KernelMerkleDistributor on a simulated "arbitrum" fork (chainid=42161)
    // 3. Build a merkle tree with leaf: keccak256(abi.encodePacked(1, Alice, 1000e18))
    // 4. Call setMerkleRoot(root) on both deployments
    // 5. Fund both distributors with 10_000e18 KERNEL
    // 6. On fork(chainid=1): Alice calls claim(1, Alice, 1000e18, proof) → succeeds
    // 7. On fork(chainid=42161): Alice calls claim(1, Alice, 1000e18, proof) → also succeeds
    // 8. Assert Alice received 2000e18 KERNEL total; Arbitrum distributor drained by 1000e18
    //    belonging to other beneficiaries
}
```

The test demonstrates that the identical `(index, account, cumulativeAmount, merkleProof)` tuple is accepted on both chains because the leaf hash is chain-agnostic and the `isClaimed` mapping is chain-local. [11](#0-10)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L90-94)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L119-123)
```text
        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L239-243)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L260-265)
```text
    {
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);

        emit Claimed(index, account, amountToSend);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-346)
```text
    function _processClaim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        internal
        returns (uint256)
    {
        UtilLib.checkNonZeroAddress(account);

        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof
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

        // Update user claim info
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
    }
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L281-298)
```text
    function _verifyClaimProof(address user, uint256 amount, bytes32[] calldata merkleProof) internal view {
        UtilLib.checkNonZeroAddress(user);

        if (merkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (amount == 0) {
            revert ZeroValueProvided();
        }

        // Verify the merkle proof
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);

        if (!isValid) {
            revert InvalidMerkleProof();
        }
```
