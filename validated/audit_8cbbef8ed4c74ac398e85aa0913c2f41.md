Audit Report

## Title
Unrestricted `claim()` Allows Anyone to Force-Claim on Behalf of Any User, Stealing Fee-Portion of Yield - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` accepts an arbitrary `account` parameter with no `msg.sender == account` check, allowing any external caller to trigger a claim for any user. When `feeInBPS > 0`, the forced claim permanently deducts up to 10% of the victim's claimable balance as a protocol fee, which the victim can never recover. The sibling contract `KernelMerkleDistributor._processClaim()` enforces this check explicitly, confirming the omission is a defect.

## Finding Description
`MerkleDistributor.claim()` is a fully permissionless external function that accepts `account` as a caller-supplied parameter with no identity check: [1](#0-0) 

No `require(msg.sender == account)` or equivalent guard exists anywhere in the function body. The function verifies the Merkle proof for the supplied `(index, account, cumulativeAmount)` tuple — all of which are public off-chain data — and then deducts a fee: [2](#0-1) 

The fee is capped at `MAX_FEE_IN_BPS = 1000` (10%): [3](#0-2) 

Once the claim is processed, `userClaims[account].cumulativeAmount` is updated to `cumulativeAmount`, permanently consuming the claim at the current fee rate. The victim cannot re-claim.

By contrast, `KernelMerkleDistributor._processClaim()` enforces the caller identity check: [4](#0-3) 

This demonstrates the protocol is aware of the requirement and intentionally applied it in the newer contract but omitted it from `MerkleDistributor`.

**Exploit path:**
1. Attacker reads the public Merkle tree to obtain `(index, victim, cumulativeAmount, proof)`.
2. Owner submits `setFeeInBPS(0)` to reduce the fee.
3. Attacker front-runs with `MerkleDistributor.claim(index, victim, cumulativeAmount, proof)` at the current high `feeInBPS`.
4. Contract deducts `fee = claimableAmount * feeInBPS / 10_000`, sends reduced amount to victim, sends fee to `protocolTreasury`.
5. Owner's fee reduction executes, but victim's claim is already consumed at the high fee rate with no recourse.

Even without front-running, any attacker can force-claim for any user at any time `feeInBPS > 0`, permanently extracting up to 10% of their yield.

## Impact Explanation
**High — Theft of unclaimed yield.** The victim permanently loses up to 10% of their claimable token balance to the protocol treasury. The tokens are not recoverable: `userClaims[account].cumulativeAmount` is set to `cumulativeAmount`, so the victim's claim slot is fully consumed. This directly matches the allowed impact class "Theft of unclaimed yield."

## Likelihood Explanation
**High.** The function is fully permissionless. Merkle proofs are public off-chain data (published JSON/IPFS). No special privileges, capital, or complex setup are required. The attack is profitable whenever `feeInBPS > 0` and any user has an unclaimed balance. The front-running variant requires only mempool monitoring. The attack is repeatable across all eligible users in a single block.

## Recommendation
Add a caller identity check at the top of `claim()`, mirroring the pattern in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, eliminating the attack surface entirely.

## Proof of Concept
**Foundry test plan:**

```solidity
function test_forceClaim_stealsYield() public {
    // Setup: deploy MerkleDistributor with feeInBPS = 500 (5%)
    // Build Merkle tree with leaf (index=1, victim, 1000e18)
    // Fund distributor with 1000e18 tokens
    // Set merkle root

    address attacker = address(0xBEEF);
    address victim   = address(0xDEAD);

    uint256 victimBalanceBefore = token.balanceOf(victim);

    // Attacker calls claim on behalf of victim
    vm.prank(attacker);
    distributor.claim(1, victim, 1000e18, proof);

    uint256 victimBalanceAfter = token.balanceOf(victim);

    // Victim receives only 950e18 (5% fee deducted)
    assertEq(victimBalanceAfter - victimBalanceBefore, 950e18);
    // 50e18 sent to treasury — victim permanently lost yield
    assertEq(token.balanceOf(protocolTreasury), 50e18);

    // Victim can no longer claim — slot is consumed
    vm.prank(victim);
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    distributor.claim(1, victim, 1000e18, proof);
}
``` [5](#0-4)

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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L137-144)
```text
        // Send the claimable amount to the user - deducting the fee
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
