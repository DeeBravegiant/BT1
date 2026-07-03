Audit Report

## Title
Missing Caller Identity Check Enables Forced Fee Deduction on Any Merkle Claimant - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never verifies that `msg.sender == account`. Because merkle proofs are public off-chain data, any external caller can force-trigger a claim on behalf of any eligible address. When `feeInBPS > 0`, the forced claim permanently deducts up to 10% of the victim's allocation as a protocol fee, constituting theft of unclaimed yield.

## Finding Description
`MerkleDistributor.claim()` is a public, permissionless function that accepts `(index, account, cumulativeAmount, merkleProof)`. [1](#0-0) 

The function verifies the merkle proof against `currentMerkleRoot`, then unconditionally deducts `feeInBPS` from `claimableAmount`, sends the remainder to `account`, and sends the fee to `protocolTreasury`. [2](#0-1) 

There is no `require(msg.sender == account)` guard anywhere in the function. The sibling contract `KernelMerkleDistributor` explicitly added this guard in `_processClaim`: [3](#0-2) 

`MerkleDistributor` never received the same fix. The fee ceiling is 10%: [4](#0-3) 

Once a forced claim executes, `userClaims[account].lastClaimedIndex` and `userClaims[account].cumulativeAmount` are permanently updated, so the victim can never reclaim the fee-deducted portion. [5](#0-4) 

## Impact Explanation
**High — Theft of unclaimed yield.** When `feeInBPS > 0`, every forced claim permanently transfers up to 10% of the victim's token allocation to `protocolTreasury` without the victim's consent. The victim's claim slot is consumed, making the loss irrecoverable. This directly matches the allowed impact: *"High. Theft of unclaimed yield."*

## Likelihood Explanation
Merkle distribution trees are published off-chain (IPFS or public API) so users can construct their own proofs. All `(index, account, cumulativeAmount, proof)` tuples are publicly readable. An attacker needs no privileged access, no governance capture, and no oracle manipulation — only gas. The attack is scriptable and repeatable for every address in the tree.

## Recommendation
Add a caller-identity check at the top of `claim()`, mirroring the fix already present in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, eliminating the attack surface entirely.

## Proof of Concept

1. Protocol deploys `MerkleDistributor` with `feeInBPS = 500` (5%) and publishes the merkle tree.
2. Alice is a valid claimant: `index=1, account=alice, cumulativeAmount=1000e18`.
3. Attacker reads the public tree, obtains Alice's proof, and calls:
   ```solidity
   merkleDistributor.claim(1, alice, 1000e18, aliceProof);
   ```
4. The contract executes without reverting — no `msg.sender` check exists.
5. `claimableAmount = 1000e18`, `fee = 50e18`, `amountToSend = 950e18`.
6. Alice receives `950e18` tokens; `50e18` tokens go to `protocolTreasury`.
7. `userClaims[alice].lastClaimedIndex = 1` — Alice's slot is permanently consumed.
8. Alice can never reclaim the 50 tokens lost to the forced fee.
9. Attacker repeats for every address in the merkle tree.

**Foundry test plan:** Deploy `MerkleDistributor` with `feeInBPS = 500`, construct a two-leaf merkle tree for Alice, call `claim()` from an attacker address with Alice's valid proof, assert Alice received `950e18` (not `1000e18`), assert `isClaimed(1, alice) == true`, and assert Alice cannot call `claim()` again to recover the fee.

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
