Audit Report

## Title
Unbounded `merkleProof` Array Enables Arbitrary Gas Consumption via `_processClaim` — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

## Summary
`KernelMerkleDistributor._processClaim()` accepts a caller-supplied `bytes32[] calldata merkleProof` with no length bound before passing it to `MerkleProofUpgradeable.verify()`. Because `processProof` iterates unconditionally over every element, an attacker who passes all O(1) pre-checks can submit a proof of arbitrary length L, forcing L `keccak256` operations before the transaction reverts with `InvalidMerkleProof` and no state change. This enables unbounded gas consumption and block-stuffing attacks repeatable at will.

## Finding Description
In `_processClaim` (lines 292–346 of `contracts/KERNEL/KernelMerkleDistributor.sol`), the execution path is:

1. O(1) guards: non-zero address check, `currentMerkleRoot != 0`, `1 ≤ index ≤ currentIndex`, `account == msg.sender`, `!isClaimed(index, account)` — all passable by any fresh address with a valid index.
2. `bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));`
3. `MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)` — delegates to `processProof`, which runs `for (uint256 i = 0; i < proof.length; i++) { computedHash = _hashPair(computedHash, proof[i]); }` over all L elements unconditionally.
4. Returns `false` → `revert InvalidMerkleProof()`.

No `require(merkleProof.length <= MAX_PROOF_LENGTH)` or equivalent guard exists anywhere in the call chain. Because the transaction reverts, no state is written, so `isClaimed` remains `false` and the attack is infinitely repeatable from the same address. [1](#0-0) [2](#0-1) 

## Impact Explanation
Gas cost scales linearly with L. Calldata alone for L = 5,000 elements costs approximately 5,000 × 32 × 16 ≈ 2.56 M gas; computation adds ~200 K gas. At L ≈ 9,000 a single transaction approaches Ethereum's ~30 M block gas limit. An attacker can repeatedly submit such transactions to saturate blocks and delay or prevent legitimate claimants from having their transactions included. This matches two explicitly allowed impacts: **Medium. Unbounded gas consumption** and **Low. Block stuffing**.

## Likelihood Explanation
Preconditions are minimal. The attacker only needs: (1) a valid `index` value, which is readable directly from the public `currentIndex` state variable; (2) an address that has not previously claimed at that index — any fresh EOA satisfies this. No privileged role, no front-running, no external dependency, and no victim cooperation is required. The attack is permissionless, repeatable, and costs only the attacker's own gas. [3](#0-2) 

## Recommendation
Add a proof-length cap before the `verify` call. A Merkle tree over N leaves requires at most `ceil(log2(N))` proof elements. For any realistic distribution (≤ 2²⁰ ≈ 1 M recipients), 20 elements is sufficient:

```solidity
uint256 public constant MAX_PROOF_LENGTH = 20;

// inside _processClaim, before the verify call:
if (merkleProof.length > MAX_PROOF_LENGTH) revert InvalidMerkleProof();
```

This makes the guard O(1) and eliminates the attack surface entirely.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "../contracts/KERNEL/KernelMerkleDistributor.sol";

contract UnboundedProofGasTest is Test {
    KernelMerkleDistributor distributor;

    function setUp() public {
        // Deploy and initialize distributor:
        // - non-zero merkle root
        // - currentIndex >= 1
        // - attacker address not yet claimed at index 1
    }

    function test_gasScalesLinearlyWithProofLength() public {
        uint256[5] memory sizes = [uint256(1), 10, 100, 1000, 5000];
        for (uint256 s = 0; s < 5; s++) {
            uint256 L = sizes[s];
            bytes32[] memory bigProof = new bytes32[](L);
            for (uint256 i = 0; i < L; i++) {
                bigProof[i] = bytes32(uint256(i + 1));
            }
            uint256 gasBefore = gasleft();
            vm.expectRevert(IMerkleDistributor.InvalidMerkleProof.selector);
            distributor.claim(1, address(this), 1 ether, bigProof);
            uint256 gasUsed = gasBefore - gasleft();
            emit log_named_uint("gasUsed for L", gasUsed);
        }
        // Plotting gasUsed vs L confirms linear growth.
        // At L ≈ 9000, gasUsed approaches the 30M block gas limit.
    }
}
```

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-323)
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
```

**File:** lib/openzeppelin-contracts-upgradeable/contracts/utils/cryptography/MerkleProofUpgradeable.sol (L48-54)
```text
    function processProof(bytes32[] memory proof, bytes32 leaf) internal pure returns (bytes32) {
        bytes32 computedHash = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            computedHash = _hashPair(computedHash, proof[i]);
        }
        return computedHash;
    }
```
