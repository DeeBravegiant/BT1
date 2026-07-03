Audit Report

## Title
Missing `msg.sender == account` Authorization in `MerkleDistributor.claim` Enables Forced Claims That Permanently Destroy Victim Yield via Fee — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

## Summary

`MerkleDistributor.claim` accepts an arbitrary `account` parameter but never verifies `msg.sender == account`. Any caller with a valid Merkle proof for a victim can force-execute the victim's claim at the current fee rate, permanently consuming the victim's allocation and routing up to 10% of their yield to `protocolTreasury`. The victim can never reclaim the fee-deducted portion, even if the owner later reduces `feeInBPS` to zero.

## Finding Description

`MerkleDistributor.claim` at [1](#0-0)  is a public, permissionless function that accepts an arbitrary `account` address. It performs Merkle proof verification, calculates the claimable delta, deducts a fee, and transfers tokens — all without any check that `msg.sender == account`.

The fee deduction and transfer at [2](#0-1)  sends `fee = (claimableAmount * feeInBPS) / 10_000` to `protocolTreasury` and only `amountToSend` to `account`. The state update at [3](#0-2)  permanently marks the allocation as consumed by setting `userClaims[account].cumulativeAmount = cumulativeAmount`, making re-claim impossible.

Merkle proofs are published publicly (IPFS / protocol frontend) as standard practice. An attacker needs only the victim's `(index, account, cumulativeAmount, merkleProof)` tuple — all publicly available — to execute the forced claim. No capital, no special role, no flash loan is required.

By contrast, the sibling contract `KernelMerkleDistributor._processClaim` explicitly enforces this invariant at [4](#0-3) , confirming the protocol's own security model requires this check. `MerkleDistributor` is a standalone deployable contract, not a base class of `KernelMerkleDistributor`, so it does not inherit this protection.

## Impact Explanation

**High — Theft of unclaimed yield.**

`feeInBPS` can be set up to `MAX_FEE_IN_BPS = 1000` (10%) at any time by the owner via [5](#0-4) . A victim who intends to wait for a fee reduction to zero before claiming is permanently deprived of up to 10% of their yield. The loss is irreversible: once `userClaims[account].cumulativeAmount` is updated, the victim's entire allocation is consumed and the fee portion is unrecoverable. This is a concrete, permanent loss of yield that falls squarely within the "Theft of unclaimed yield" impact class.

## Likelihood Explanation

**Medium.** Merkle proofs for all eligible accounts are published publicly as a standard requirement for Merkle distribution UIs. The attack requires no capital, no privileged role, and no victim interaction — only gas and the victim's public proof tuple. A rational griefing scenario exists: a bot can sweep all pending claims the moment `feeInBPS > 0`, forcing every holder to pay the maximum fee before any fee reduction takes effect. The attack is repeatable across all claimants in a single block.

## Recommendation

Add a caller authorization check at the top of `claim`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim`:

```solidity
if (account != msg.sender) revert Unauthorized();
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, as `KernelTop100MerkleDistributor` does.

## Proof of Concept

1. Deploy `MerkleDistributor` with `feeInBPS = 500` (5%).
2. Owner sets a Merkle root; Alice's allocation of 1,000 tokens is published at `(index=1, alice, 1000e18, proof)`.
3. Alice waits, expecting the owner to call `setFeeInBPS(0)`.
4. Bob calls `claim(1, alice, 1000e18, proof)` before the fee reduction.
5. `fee = 1000e18 * 500 / 10_000 = 50e18` is sent to `protocolTreasury`; Alice receives `950e18`.
6. `userClaims[alice].cumulativeAmount` is set to `1000e18` — Alice's allocation is fully consumed.
7. Owner later calls `setFeeInBPS(0)`; Alice calls `claim` and receives `AlreadyClaimed` revert.
8. Alice permanently loses 50 tokens she would have received in full.

Foundry test outline:
```solidity
function test_forcedClaim_stealsYield() public {
    // Setup: deploy MerkleDistributor with feeInBPS=500, set merkle root
    // Alice has valid proof for 1000e18
    vm.prank(bob);
    distributor.claim(1, alice, 1000e18, aliceProof);
    assertEq(token.balanceOf(alice), 950e18);
    assertEq(token.balanceOf(treasury), 50e18);
    // Alice can no longer claim
    vm.prank(alice);
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    distributor.claim(1, alice, 1000e18, aliceProof);
}
```

### Citations

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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L134-135)
```text
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L198-206)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        feeInBPS = _feeInBPS;

        emit FeeInBPSUpdated(_feeInBPS);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
