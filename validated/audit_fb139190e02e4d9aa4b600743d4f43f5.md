### Title
Missing `msg.sender == account` Validation Allows Anyone to Force-Claim on Behalf of Any User - (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` parameter without verifying `msg.sender == account`, allowing any external caller to consume any user's claim slot and force token distribution at an undesired time, including at a higher fee than the user intended to pay.

### Finding Description

`MerkleDistributor.claim()` accepts `account` as a caller-supplied parameter and transfers tokens to that address with no check that `msg.sender == account`:

```solidity
function claim(
    uint256 index,
    address account,       // ← caller-supplied, never validated against msg.sender
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    // ...
    bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
    if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
        revert InvalidMerkleProof();
    }
    // ...
    IERC20(token).safeTransfer(account, amountToSend);   // tokens go to account, not msg.sender
    IERC20(token).safeTransfer(protocolTreasury, fee);
```

The merkle proof and all parameters needed to call this function are fully public (derivable from on-chain data or the off-chain merkle tree). Any attacker can call `claim(index, victimAddress, cumulativeAmount, victimProof)` and consume the victim's claim slot.

The sibling contract `KernelMerkleDistributor` explicitly guards against this with `if (account != msg.sender) { revert Unauthorized(); }` in `_processClaim()`, demonstrating the protocol's own recognition that this check is required. `MerkleDistributor` omits it entirely.

`MerkleBlastPointsDistributor.claim()` has the identical missing check, though its impact is limited to event-based griefing since no tokens are transferred there.

### Impact Explanation

**Low. Contract fails to deliver promised returns, but doesn't lose value.**

Users lose the ability to choose *when* they receive their tokens. In the concrete worst case: if `feeInBPS` is non-zero (up to the 10% maximum) and the owner has announced or is about to execute a fee reduction, an attacker can front-run the fee reduction by force-claiming for all users at the current higher fee. The fee portion (`claimableAmount * feeInBPS / 10_000`) is permanently redirected to `protocolTreasury` rather than remaining claimable by the user after the fee drops. The user receives fewer tokens than they would have received by waiting.

### Likelihood Explanation

High. The attack requires no special privileges, no funds, and no private information. The merkle proof for any `account` is derivable from the publicly posted merkle tree. Any external caller can execute this against any user at any time the contract is unpaused.

### Recommendation

Add the same guard already present in `KernelMerkleDistributor._processClaim()`:

```solidity
if (account != msg.sender) revert Unauthorized();
```

Apply this to both `MerkleDistributor.claim()` and `MerkleBlastPointsDistributor.claim()`.

### Proof of Concept

1. Alice holds a valid merkle proof for `(index=5, account=alice, cumulativeAmount=1000e18)`. Current `feeInBPS = 1000` (10%). Owner has announced a fee reduction to 0% in the next transaction.
2. Bob (attacker) reads Alice's proof from the off-chain merkle tree and calls:
   ```solidity
   merkleDistributor.claim(5, alice, 1000e18, aliceProof);
   ```
3. The call succeeds. Alice receives `900e18` tokens; `100e18` goes to `protocolTreasury`.
4. Alice's `userClaims[alice].lastClaimedIndex` is now `5` and `cumulativeAmount` is `1000e18`. Her claim slot is consumed.
5. The owner's fee reduction executes. Alice can no longer reclaim the `100e18` she lost to the fee — she would have received the full `1000e18` had she been allowed to claim after the fee dropped.
6. Alice's only recourse is to wait for the next merkle root update with a higher `cumulativeAmount`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-121)
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

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L86-114)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeBlastPointAmount,
        uint256 cumulativeBlastGoldAmount,
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
        bytes32 node =
            keccak256(abi.encodePacked(index, account, cumulativeBlastPointAmount, cumulativeBlastGoldAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```
