Audit Report

## Title
Missing `msg.sender` Validation in `claim()` Enables Forced Fee-Bearing Claims on Behalf of Any User - (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

## Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but performs no check that `msg.sender == account`. Any external caller who possesses a valid merkle proof for a victim can force a claim on their behalf at the current `feeInBPS` rate (up to 10%), permanently diverting a portion of the victim's unclaimed yield to `protocolTreasury`. The sibling contract `KernelMerkleDistributor` already guards against this with an explicit `account != msg.sender` revert.

## Finding Description
`MerkleDistributor.claim()` is publicly callable with no caller restriction: [1](#0-0) 

After verifying the merkle proof against `(index, account, cumulativeAmount)`, the function irreversibly deducts a fee and sends it to `protocolTreasury`: [2](#0-1) 

There is no check equivalent to the one in `KernelMerkleDistributor._processClaim()`: [3](#0-2) 

Merkle tree data (indices, accounts, cumulative amounts, proofs) is routinely published off-chain or derivable from on-chain events, so any EOA can reconstruct a valid proof for any victim. The attacker calls `claim(index, victim, cumulativeAmount, victimProof)`, the proof verifies, the fee is deducted, and the victim's `userClaims` state is updated — preventing any future reclaim of the lost fee amount.

## Impact Explanation
**High — Theft of unclaimed yield.** `feeInBPS` can be set up to `MAX_FEE_IN_BPS = 1000` (10%): [4](#0-3) 

A user holding off on claiming while the fee is elevated (waiting for the owner to lower it) can be front-run by an attacker who forces the claim at the high-fee moment. The fee is permanently sent to `protocolTreasury` and cannot be recovered. For a user entitled to 10,000 tokens at 10% fee, this is a forced, irreversible loss of 1,000 tokens of unclaimed yield.

## Likelihood Explanation
**High.** No special privilege is required — any EOA can call `claim()`. Merkle proof data is publicly available. The attacker is economically incentivized whenever `feeInBPS > 0`, and the owner can raise the fee at any time up to 10% before the forced claim executes. The attack is repeatable across all users in the merkle tree.

## Recommendation
Add a caller check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

This ensures only the rightful beneficiary can trigger their own claim and choose the timing (and thus the fee rate) that applies to them.

## Proof of Concept
1. Owner sets `feeInBPS = 1000` (10%).
2. Alice is entitled to 10,000 tokens; her merkle proof is publicly derivable from the published tree.
3. Alice decides to wait, expecting the owner to lower the fee.
4. Bob (attacker) calls `MerkleDistributor.claim(index, alice, 10_000e18, aliceProof)`.
5. The call succeeds: the merkle proof verifies, `userClaims[alice]` is updated, Alice receives 9,000 tokens, and 1,000 tokens are sent to `protocolTreasury` as fee.
6. Alice cannot reclaim the 1,000 tokens — her `cumulativeAmount` is already recorded at the full `10_000e18`.

**Foundry test sketch:**
```solidity
function test_forcedClaimOnBehalf() public {
    // Setup: set feeInBPS = 1000, set merkle root, fund distributor
    distributor.setFeeInBPS(1000);
    distributor.setMerkleRoot(root);
    token.transfer(address(distributor), 10_000e18);

    // Attacker forces Alice's claim
    vm.prank(bob);
    distributor.claim(aliceIndex, alice, 10_000e18, aliceProof);

    // Alice received only 9,000 tokens; 1,000 went to treasury
    assertEq(token.balanceOf(alice), 9_000e18);
    assertEq(token.balanceOf(protocolTreasury), 1_000e18);

    // Alice cannot reclaim
    vm.prank(alice);
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    distributor.claim(aliceIndex, alice, 10_000e18, aliceProof);
}
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-106)
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
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
