Audit Report

## Title
Permissionless `claim()` allows any caller to force-claim on behalf of any user, extracting protocol fees from their allocation - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` accepts a caller-supplied `account` parameter with no `msg.sender == account` guard. Any external caller who possesses a valid merkle proof for a victim can trigger the victim's claim, causing the protocol fee (`feeInBPS`, up to 10%) to be permanently deducted from the victim's allocation and sent to `protocolTreasury`. The victim's claim slot is then consumed, preventing them from ever reclaiming the lost tokens.

## Finding Description
`MerkleDistributor.claim()` is a public, permissionless function:

```solidity
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

There is no identity check anywhere in the function. After proof verification, the contract deducts a fee and marks the claim as consumed:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
``` [2](#0-1) 

The claim state is permanently updated before the transfer:

```solidity
userClaims[account].lastClaimedIndex = index;
userClaims[account].cumulativeAmount = cumulativeAmount;
``` [3](#0-2) 

The sibling contract `KernelMerkleDistributor` explicitly guards against this in `_processClaim`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

This guard is entirely absent from `MerkleDistributor`. The merkle leaf is `keccak256(abi.encodePacked(index, account, cumulativeAmount))`, so the proof is tied to the victim's address and amount — both of which are public off-chain data published by the protocol. An attacker needs no special privileges or capital; they only need to read the published proof data and submit a single transaction. [5](#0-4) 

## Impact Explanation
**High — Theft of unclaimed yield.**

The victim's allocation is reduced by up to `MAX_FEE_IN_BPS = 1000` (10%) relative to what they would have received had they chosen their own claim timing. [6](#0-5) 

The fee is irrevocably transferred to `protocolTreasury`; the victim cannot recover it. Once the claim is consumed, any subsequent call by the victim reverts with `AlreadyClaimed`. This is a direct, permanent loss of entitled yield tokens, matching the allowed impact: **High. Theft of unclaimed yield.**

## Likelihood Explanation
**High.** Merkle proofs for all eligible accounts are published off-chain by the protocol (standard practice for merkle distributors). Any external caller can read the proof data, construct a valid call, and submit it in a single transaction with no special privileges, capital, or prior interaction with the protocol. The attack is repeatable for every eligible address in the merkle tree simultaneously.

## Recommendation
Add a caller-identity check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```diff
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
+    if (account != msg.sender) revert Unauthorized();
     ...
 }
```

## Proof of Concept
1. Protocol publishes merkle root containing `(index=1, victim=0xVICTIM, cumulativeAmount=1000e18)` and the corresponding proof off-chain.
2. Attacker calls:
   ```solidity
   merkleDistributor.claim(1, 0xVICTIM, 1000e18, victimProof);
   ```
3. Contract verifies the proof (valid), deducts `fee = 1000e18 * feeInBPS / 10_000`, transfers `amountToSend` to `0xVICTIM` and `fee` to `protocolTreasury`.
4. `userClaims[0xVICTIM].lastClaimedIndex` is set to `1`; the victim's claim for this index is permanently consumed.
5. Victim calls `claim()` themselves — reverts with `AlreadyClaimed`.
6. Victim has permanently lost `fee` tokens they were entitled to receive in full.

**Foundry test sketch:**
```solidity
function testForceClaim() public {
    // Setup: fund distributor, set merkle root with victim leaf
    vm.prank(attacker);
    distributor.claim(1, victim, 1000e18, victimProof);

    // Victim's claim is consumed; they lost the fee
    assertEq(token.balanceOf(victim), 1000e18 * (10_000 - feeInBPS) / 10_000);

    vm.prank(victim);
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    distributor.claim(1, victim, 1000e18, victimProof);
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L120-121)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
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
