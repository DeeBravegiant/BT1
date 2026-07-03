Audit Report

## Title
Unauthorized Claim Triggering Causes Forced Fee Extraction from Users - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
The `claim` function in `MerkleDistributor.sol` accepts an arbitrary `account` parameter but never validates that `msg.sender == account`. Any caller possessing a valid merkle proof for a target user can trigger that user's claim, forcing the protocol fee deduction (up to 10%) on the victim's unclaimed rewards without their consent. The fee is permanently diverted to `protocolTreasury`, and the claim is marked processed at the current `cumulativeAmount`, leaving the user with no recourse.

## Finding Description
In `MerkleDistributor.sol`, the `claim` function is fully public and accepts an arbitrary `account` address: [1](#0-0) 

There is no check that `msg.sender == account` anywhere in the function body. After verifying the merkle proof, the contract computes and deducts a fee: [2](#0-1) 

The fee is sent to `protocolTreasury` and the remainder to `account`. The claim is then permanently marked as processed at the current `cumulativeAmount`.

By contrast, `KernelMerkleDistributor.sol` explicitly enforces caller identity in `_processClaim`: [3](#0-2) 

This confirms the intended design is that only the account owner may trigger their own claim. The omission in `MerkleDistributor.sol` is a directly exploitable discrepancy.

Merkle proofs are published off-chain for users to self-claim, making them publicly accessible to any attacker. The attacker needs no capital, no special role, and no victim cooperation.

## Impact Explanation
**High — Theft of unclaimed yield.**

When `feeInBPS > 0` (up to `MAX_FEE_IN_BPS = 1000`, i.e. 10%), an attacker who forces a claim extracts the fee from the victim's unclaimed yield and permanently routes it to `protocolTreasury`. The victim receives only `claimableAmount - fee` instead of the full `claimableAmount` they would have received had they waited for a fee reduction or fee waiver. The fee cannot be recovered once the claim is processed. [4](#0-3) 

## Likelihood Explanation
Any unprivileged external caller can execute this attack. The only precondition is possession of a valid merkle proof for the target user, which is routinely published off-chain as part of the distribution mechanism. The attack requires no capital, no special permissions, and can be repeated against any user with unclaimed rewards at any time the contract is unpaused. The attacker bears no cost beyond gas.

## Recommendation
Add a caller validation check in `MerkleDistributor.sol`'s `claim` function, consistent with `KernelMerkleDistributor.sol`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

This should be placed immediately after the `isClaimed` check, before any state changes or token transfers. [5](#0-4) 

## Proof of Concept

1. Deploy `MerkleDistributor` with `feeInBPS = 1000` (10%).
2. Set a merkle root that includes Alice's entry: `(index=1, alice, cumulativeAmount=1000e18)`.
3. Publish the merkle proof off-chain (standard distribution practice).
4. Attacker calls `claim(1, alice, 1000e18, aliceProof)` from any EOA.
5. Contract executes: `fee = 100e18`, `amountToSend = 900e18`.
6. `protocolTreasury` receives `100e18`; Alice receives `900e18`.
7. `userClaims[alice].cumulativeAmount = 1000e18` — claim is permanently marked processed.
8. Alice has lost `100e18` (10% of her entitled rewards) with no recourse.

**Foundry test sketch:**
```solidity
function testForcedClaimExtractsFee() public {
    // Setup: deploy distributor with feeInBPS=1000, fund it, set merkle root
    // Generate alice's proof off-chain
    vm.prank(attacker);
    distributor.claim(1, alice, 1000e18, aliceProof);
    assertEq(token.balanceOf(alice), 900e18);          // Alice loses 10%
    assertEq(token.balanceOf(protocolTreasury), 100e18); // Fee extracted
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L115-117)
```text
        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
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
