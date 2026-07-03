Audit Report

## Title
Missing `msg.sender == account` Check in `MerkleDistributor.claim()` Allows Force-Claiming on Behalf of Any User, Extracting Protocol Fee Without Consent - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` accepts a caller-supplied `account` parameter but never verifies that `msg.sender == account`. Any unprivileged external caller can supply a valid public merkle proof and force-trigger a claim for any user, permanently consuming the victim's claim slot and extracting the protocol fee (up to 10%) from their allocation without consent. The sibling contract `KernelMerkleDistributor` correctly guards against this in `_processClaim()` with an explicit `account != msg.sender` revert.

## Finding Description
In `contracts/utils/MerkleDistributor/MerkleDistributor.sol`, the `claim()` function (lines 97–147) accepts `account` as a caller-supplied parameter, verifies the merkle proof against it, updates `userClaims[account]`, and transfers tokens — but contains zero references to `msg.sender` anywhere in the file:

```solidity
function claim(
    uint256 index,
    address account,       // caller-supplied, never verified against msg.sender
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    // ... no msg.sender == account check ...
    userClaims[account].lastClaimedIndex = index;
    userClaims[account].cumulativeAmount = cumulativeAmount;

    uint256 fee = (claimableAmount * feeInBPS) / 10_000;
    uint256 amountToSend = claimableAmount - fee;

    IERC20(token).safeTransfer(account, amountToSend);
    IERC20(token).safeTransfer(protocolTreasury, fee);  // fee extracted from victim
}
```

The sibling contract `contracts/KERNEL/KernelMerkleDistributor.sol` correctly guards this in `_processClaim()` at lines 311–313:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

The existing checks (`isClaimed`, `InvalidMerkleProof`, `InvalidIndex`) are all insufficient — they validate the proof data, not the identity of the caller. A valid proof is public off-chain data, so any caller can pass all existing checks for any victim.

## Impact Explanation
**High — Theft of unclaimed yield.**

The protocol fee (`feeInBPS`, up to `MAX_FEE_IN_BPS = 1000`, i.e. 10%) is deducted from the victim's claimable allocation and sent to `protocolTreasury`. The victim's claim slot is permanently consumed (`userClaims[account].lastClaimedIndex` is updated), preventing any future re-claim. A user who was waiting for the owner to call `setFeeInBPS(0)` before claiming permanently loses up to 10% of their entitled token allocation — yield that was unclaimed and is now irreversibly extracted without their knowledge or consent. This matches the allowed impact: **High. Theft of unclaimed yield.**

## Likelihood Explanation
**High.** The attack requires no special role, no capital, and no front-running dependency beyond timing. The only inputs needed — `index`, `account`, `cumulativeAmount`, and `merkleProof` — are all public off-chain data published by the protocol for self-claiming. Any unprivileged external caller can execute this against any unclaimed account at any time the contract is unpaused, and can repeat it across all unclaimed accounts in a single block.

## Recommendation
Add a caller ownership check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
+   if (account != msg.sender) revert Unauthorized();
    // ...rest of logic
}
```

Also add `Unauthorized` to the `IMerkleDistributor` interface defined in `MerkleDistributor.sol` (currently absent, unlike the one in `KernelMerkleDistributor.sol`).

## Proof of Concept
1. Protocol publishes a merkle tree. Alice's leaf: `(index=5, account=Alice, cumulativeAmount=1000e18)`. Merkle proof is public.
2. Current `feeInBPS = 500` (5%). Alice is waiting for the owner to call `setFeeInBPS(0)`.
3. Attacker calls `MerkleDistributor.claim(5, Alice, 1000e18, aliceProof)`.
4. Contract verifies proof (valid), updates `userClaims[Alice]`, transfers `950e18` to Alice, transfers `50e18` to `protocolTreasury`.
5. Alice's claim is permanently consumed. She cannot reclaim. She has lost `50e18` tokens she would have received had she been allowed to claim after the fee was zeroed.

**Foundry test plan:**
```solidity
function testForceClaimExtractsFee() public {
    // Setup: deploy MerkleDistributor, set merkle root, fund contract
    // Alice's leaf: (index=1, account=alice, cumulativeAmount=1000e18)
    // feeInBPS = 500

    uint256 aliceBalanceBefore = token.balanceOf(alice);
    uint256 treasuryBalanceBefore = token.balanceOf(protocolTreasury);

    vm.prank(attacker); // attacker, not alice
    distributor.claim(1, alice, 1000e18, aliceProof);

    // Alice received only 950e18, not 1000e18
    assertEq(token.balanceOf(alice) - aliceBalanceBefore, 950e18);
    // Treasury received 50e18 fee from Alice's allocation
    assertEq(token.balanceOf(protocolTreasury) - treasuryBalanceBefore, 50e18);
    // Alice's claim is permanently consumed
    assertTrue(distributor.isClaimed(1, alice));
}
```