Audit Report

## Title
Missing `msg.sender == account` Authorization in `MerkleDistributor.claim` Enables Forced Claims, Stealing User Yield via Fee — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

## Summary

`MerkleDistributor.claim` accepts an arbitrary `account` parameter but performs no check that `msg.sender == account`. Because Merkle proofs are published publicly, any caller can force-execute a claim on behalf of any victim at the current `feeInBPS` rate. The fee is permanently deducted from the victim's allocation and sent to `protocolTreasury`, and the victim's `userClaims` state is updated so the allocation can never be re-claimed — permanently destroying up to 10% of their yield.

## Finding Description

`MerkleDistributor.claim` is a permissionless external function that accepts `account` as a caller-supplied parameter: [1](#0-0) 

There is no authorization check anywhere in the function body. After verifying the Merkle proof, the contract permanently updates the victim's claim state: [2](#0-1) 

Then deducts the fee and transfers the net amount to `account`: [3](#0-2) 

The sibling contract `KernelMerkleDistributor._processClaim` explicitly enforces the invariant that is missing here: [4](#0-3) 

`MerkleDistributor` is a standalone contract, not a base class of `KernelMerkleDistributor`, so this fix was never inherited. The `feeInBPS` can be set up to `MAX_FEE_IN_BPS = 1000` (10%) by the owner at any time: [5](#0-4) 

**Exploit path:**
1. Owner deploys `MerkleDistributor` with `feeInBPS = 500` (5%).
2. Alice's `(index, address, cumulativeAmount, proof)` tuple is published on the distribution page.
3. Alice waits, expecting the owner to call `setFeeInBPS(0)`.
4. Bob calls `claim(index, alice, 1000e18, proof)` before the fee reduction.
5. Alice receives 950 tokens; 50 tokens flow to `protocolTreasury`.
6. `userClaims[alice].cumulativeAmount` is now `1000e18` — Alice can never reclaim the 50 tokens.

## Impact Explanation

**High — Theft of unclaimed yield.**

The victim permanently loses up to 10% of their claimable allocation. The loss is irreversible: once `userClaims[account].cumulativeAmount` is updated to `cumulativeAmount`, the `AlreadyClaimed` check prevents any future claim on the same allocation. The victim had a legitimate expectation of receiving the full amount by waiting for a fee reduction, which is a protocol-supported owner action via `setFeeInBPS`. [6](#0-5) 

## Likelihood Explanation

**Medium.** Merkle proofs for all eligible accounts are published publicly as standard practice for all Merkle distribution deployments. The attack requires no special role, no capital, and no flash loan — only knowledge of the victim's proof tuple, which is freely available. A rational griefing scenario exists: a bot can sweep all pending claims the moment `feeInBPS > 0`, forcing every holder to pay the maximum fee before any reduction takes effect. The attack is repeatable across all claimants in a single block.

## Recommendation

Add a caller authorization check at the top of `claim`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim`:

```solidity
if (account != msg.sender) revert Unauthorized();
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, as `KernelTop100MerkleDistributor` does. [4](#0-3) 

## Proof of Concept

**Foundry test plan:**

```solidity
function test_forcedClaim_stealsYield() public {
    // Setup: deploy MerkleDistributor with feeInBPS = 500
    // Build merkle tree with alice's allocation of 1000e18
    // Set merkle root; set feeInBPS = 500

    uint256 aliceBalanceBefore = token.balanceOf(alice);
    uint256 treasuryBalanceBefore = token.balanceOf(treasury);

    // Bob (attacker) calls claim on behalf of Alice
    vm.prank(bob);
    distributor.claim(index, alice, 1000e18, aliceProof);

    // Alice receives only 950e18 (5% fee deducted)
    assertEq(token.balanceOf(alice) - aliceBalanceBefore, 950e18);
    // Treasury receives 50e18
    assertEq(token.balanceOf(treasury) - treasuryBalanceBefore, 50e18);

    // Alice can no longer claim — AlreadyClaimed
    vm.prank(alice);
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    distributor.claim(index, alice, 1000e18, aliceProof);

    // Owner sets fee to 0 — too late for Alice
    vm.prank(owner);
    distributor.setFeeInBPS(0);
    // Alice still cannot claim; her allocation is permanently consumed
}
``` [7](#0-6)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-51)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;

    address public override token;
    address public protocolTreasury;
    uint256 public feeInBPS;
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L115-117)
```text
        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
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
