### Title
Missing Caller Authorization in `MerkleDistributor.claim()` Allows Anyone to Force Claims on Behalf of Any User, Enabling Theft of Unclaimed Yield - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter but never verifies that `msg.sender == account`. Any external caller who possesses a valid Merkle proof for a target user can trigger that user's claim, forcing token distribution (minus the protocol fee) to the target and permanently updating their claim state. This is the direct analog of the TokTokNft authorization bypass: a function that transfers funds on behalf of a specific user can be executed by anyone. The sibling contract `KernelMerkleDistributor` already enforces this check, confirming the protocol team is aware of the pattern but failed to apply it consistently.

---

### Finding Description

In `MerkleDistributor.claim()`, the function accepts `account` as a caller-supplied parameter, verifies the Merkle proof against `(index, account, cumulativeAmount)`, transfers `claimableAmount - fee` to `account`, transfers `fee` to `protocolTreasury`, and updates `userClaims[account]` — all without any check that `msg.sender == account`. [1](#0-0) 

The `feeInBPS` is a configurable parameter, settable by the owner up to `MAX_FEE_IN_BPS = 1000` (10%). [2](#0-1) 

The fee deduction is applied unconditionally at claim time: [3](#0-2) 

By contrast, `KernelMerkleDistributor._processClaim()` explicitly enforces the caller check: [4](#0-3) 

Merkle proof data is typically published off-chain (public JSON or IPFS), making it trivially accessible to any attacker for any target address.

---

### Impact Explanation

**Theft of unclaimed yield (High):** The `feeInBPS` is owner-configurable and can be up to 10%. A user who is waiting for the fee to be reduced (e.g., from 10% to 0%) before claiming can have their claim forced by an attacker while the fee is still high. The user permanently loses the fee amount — up to 10% of their entire allocation — that they could have received fee-free. Once `userClaims[account]` is updated, the user cannot re-claim for the same index, so the loss is irreversible. [5](#0-4) 

**Forced claim timing (Low):** Even when the fee is constant, a user (particularly a smart contract integrator) may have specific reasons to delay claiming. Forcing a claim violates the user's autonomy and can break downstream accounting or integration logic that expects to control when tokens are received.

---

### Likelihood Explanation

- Merkle proof data is published off-chain and publicly accessible to any party.
- The attacker requires no special permissions, no capital, and no privileged access.
- The fee is configurable and is non-zero in normal protocol operation (up to 10%).
- The scenario where a user waits for a fee reduction is realistic: the owner can call `setFeeInBPS()` at any time, and users have an incentive to time their claims accordingly. [6](#0-5) 

---

### Recommendation

Add a caller authorization check to `MerkleDistributor.claim()`, matching the pattern already implemented in `KernelMerkleDistributor`:

```diff
+ error Unauthorized();

 function claim(
     uint256 index,
     address account,
     uint256 cumulativeAmount,
     bytes32[] calldata merkleProof
 ) external override whenNotPaused {
+    if (msg.sender != account) revert Unauthorized();
     // ... rest of function unchanged
 }
```

---

### Proof of Concept

```solidity
function test_forceClaim_stealsYieldVieFeeReduction() public {
    address alice = address(0xA11CE);
    address attacker = address(0xBAD);

    // Protocol has feeInBPS = 1000 (10%)
    // Alice has 100e18 tokens claimable; she is waiting for fee to drop to 0

    uint256 aliceBalanceBefore = token.balanceOf(alice);

    // Attacker obtains alice's public merkle proof and forces her claim
    vm.prank(attacker);
    merkleDistributor.claim(index, alice, 100e18, aliceProof);

    // Alice receives only 90e18 tokens; 10e18 goes to protocolTreasury as fee
    assertEq(token.balanceOf(alice), aliceBalanceBefore + 90e18);

    // Owner later reduces fee to 0%
    vm.prank(owner);
    merkleDistributor.setFeeInBPS(0);

    // Alice tries to claim again — reverts because her state was already updated
    vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
    vm.prank(alice);
    merkleDistributor.claim(index, alice, 100e18, aliceProof);

    // Alice permanently lost 10e18 tokens (10% fee) she could have received fee-free
}
```

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-51)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;

    address public override token;
    address public protocolTreasury;
    uint256 public feeInBPS;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-147)
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
        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim info, and send the token.
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);

        emit Claimed(index, account, claimableAmount);
    }
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
