### Title
Anyone Can Force Fee Deduction on Any User's Reward Claim — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter with no `msg.sender == account` guard. Any unprivileged caller who knows a victim's merkle proof (which is public off-chain data) can trigger the victim's claim at any time, immediately deducting the non-refundable `feeInBPS` fee from the victim's claimable reward tokens and sending it irrecoverably to the protocol treasury — without the victim's consent.

### Finding Description

`MerkleDistributor.claim()` is a permissionless external function that accepts an `account` address supplied by the caller:

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

There is no check that `msg.sender == account`. The function proceeds to deduct the fee and transfer it to the treasury:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
``` [2](#0-1) 

The fee is up to 10% (`MAX_FEE_IN_BPS = 1000`) and is sent directly to `protocolTreasury` with no refund path. Once the claim is processed, `userClaims[account].cumulativeAmount` is updated, permanently marking that tranche as consumed. [3](#0-2) 

By contrast, the sibling contract `KernelMerkleDistributor._processClaim()` correctly enforces `account != msg.sender` → `revert Unauthorized()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

`MerkleDistributor` is missing this protection entirely.

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

A victim user's claimable reward tokens are reduced by up to 10% (`feeInBPS / 10_000 × claimableAmount`) without their knowledge or consent. The fee is irrecoverably transferred to the protocol treasury. The victim cannot reclaim it. If the protocol owner intends to lower `feeInBPS` in the future, an attacker can race to force the victim's claim at the current higher rate, permanently extracting more yield than the victim would have paid voluntarily. [2](#0-1) 

### Likelihood Explanation

**Likelihood: High.**

Merkle proofs are computed from publicly available off-chain data (the merkle tree is published so users can construct their own proofs). Any external caller can reconstruct a valid `(index, account, cumulativeAmount, merkleProof)` tuple for any eligible address and submit the transaction. No special privilege, leaked key, or oracle manipulation is required. The only precondition is that the contract is not paused and the victim has an unclaimed balance.

### Recommendation

Add a caller-identity check identical to the one already present in `KernelMerkleDistributor`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [4](#0-3) 

Insert this guard at the top of `MerkleDistributor.claim()`, before any state changes or transfers.

### Proof of Concept

1. Protocol deploys `MerkleDistributor` with `feeInBPS = 500` (5%).
2. Alice is entitled to `1000e18` reward tokens; her merkle proof is publicly derivable.
3. Bob (attacker) calls `claim(index, alice, 1000e18, aliceProof)`.
4. Contract deducts `fee = 50e18`, sends `950e18` to Alice, and `50e18` to `protocolTreasury`.
5. Alice's claim is permanently marked consumed at this cumulative amount.
6. Alice loses 50 tokens she never consented to pay as a fee, with no recourse.

If the owner had planned to call `setFeeInBPS(0)` the next block, Alice would have received the full `1000e18`. Bob's forced claim permanently steals the 50-token difference. [5](#0-4)

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
