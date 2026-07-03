Audit Report

## Title
Permissionless `claim()` Enables Forced Fee Extraction on Any User's Unclaimed Yield — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

## Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter with no check that `msg.sender == account`. Because merkle proofs are published off-chain for users to self-claim, any unprivileged caller can reconstruct a valid proof for any leaf and force a victim's claim at the current `feeInBPS` rate. The fee (up to 10%) is permanently transferred to `protocolTreasury`, reducing the victim's unclaimed yield with no recourse.

## Finding Description

`MerkleDistributor.claim()` is a fully public function:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol L97-106
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
``` [1](#0-0) 

There is no `msg.sender == account` guard anywhere in the function body. After proof verification, the fee is computed and permanently routed to `protocolTreasury`:

```solidity
// L138-144
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;
IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
``` [2](#0-1) 

`feeInBPS` is owner-controlled up to `MAX_FEE_IN_BPS = 1000` (10%): [3](#0-2) 

By contrast, `KernelMerkleDistributor._processClaim()` explicitly enforces the caller check:

```solidity
// contracts/KERNEL/KernelMerkleDistributor.sol L311-313
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

This check was deliberately added to `KernelMerkleDistributor` but is absent from `MerkleDistributor`. The exploit path is:

1. Owner sets `feeInBPS = 1000` (10%).
2. Alice holds a valid merkle leaf and defers her claim, waiting for the fee to drop.
3. Bob reconstructs Alice's proof from the publicly available merkle tree data.
4. Bob calls `MerkleDistributor.claim(index, Alice, cumulativeAmount, aliceProof)`.
5. The contract deducts `fee = claimableAmount * 10%` and sends it to `protocolTreasury`; Alice receives only 90%.
6. Alice's claim is now marked as processed (`userClaims[account].lastClaimedIndex` updated at L134); she cannot reclaim the lost fee. [5](#0-4) 

## Impact Explanation

**High — Theft of unclaimed yield.** The fee portion of the victim's unclaimed yield (up to 10%) is permanently diverted to `protocolTreasury` without the victim's consent. The victim's claim state is updated, making the loss irreversible. This directly matches the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation

**Medium.** The attacker requires no special role, no capital, and no privileged access — only the ability to call a public function with a valid merkle proof. Merkle proofs are necessarily public (users need them to self-claim). The attack is only harmful when `feeInBPS > 0` and the victim is deferring their claim in anticipation of a fee reduction, which is a realistic user behavior given the owner's ability to change `feeInBPS` at any time via `setFeeInBPS()`. [6](#0-5) 

## Recommendation

Add the same caller check already present in `KernelMerkleDistributor._processClaim()` at the top of `MerkleDistributor.claim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Also declare the `Unauthorized` error in `IMerkleDistributor` (it is currently missing from `MerkleDistributor.sol`'s interface). [7](#0-6) 

## Proof of Concept

**Foundry test outline:**

```solidity
function test_forcedClaimDrainsYield() public {
    // Setup: deploy MerkleDistributor with feeInBPS = 1000 (10%)
    // Build merkle tree with leaf (index=1, alice, 10_000e18)
    // Fund distributor, set merkle root

    uint256 aliceBalanceBefore = token.balanceOf(alice);
    uint256 treasuryBalanceBefore = token.balanceOf(treasury);

    // Bob (unprivileged) calls claim on behalf of Alice
    vm.prank(bob);
    distributor.claim(1, alice, 10_000e18, aliceProof);

    // Alice receives only 9_000e18 (fee deducted without her consent)
    assertEq(token.balanceOf(alice) - aliceBalanceBefore, 9_000e18);
    // Treasury receives 1_000e18 fee
    assertEq(token.balanceOf(treasury) - treasuryBalanceBefore, 1_000e18);

    // Alice can no longer claim — her index is already marked
    vm.prank(alice);
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    distributor.claim(1, alice, 10_000e18, aliceProof);
}
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L12-19)
```text
interface IMerkleDistributor {
    error ZeroValueProvided();
    error NoTokensToClaim();
    error AlreadyClaimed();
    error InvalidMerkleProof();
    error InvalidIndex();
    error InvalidFeeInBPS();

```

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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L133-135)
```text
        // Update user claim info, and send the token.
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
