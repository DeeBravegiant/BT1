### Title
Unrestricted `claim` Caller Enables Forced Fee Deduction on Any User's Unclaimed Yield — (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim` imposes no restriction on who may call it on behalf of a given `account`. Any external caller can supply a valid merkle proof for a victim and trigger their claim, permanently deducting the configured protocol fee from the victim's entitled tokens. The sister contract `KernelMerkleDistributor` correctly guards against this with an explicit `msg.sender == account` check; `MerkleDistributor` does not.

---

### Finding Description

`MerkleDistributor.claim` accepts an arbitrary `account` parameter and transfers the post-fee amount to that address: [1](#0-0) 

There is no check that `msg.sender == account`. The function verifies the merkle proof, deducts the fee, marks the claim as consumed, and sends tokens — all without confirming the caller is the beneficiary.

By contrast, `KernelMerkleDistributor._processClaim` explicitly enforces caller identity: [2](#0-1) 

The fee ceiling is 10% (`MAX_FEE_IN_BPS = 1000`): [3](#0-2) 

The fee computation and transfer: [4](#0-3) 

Once a claim is processed, `userClaims[account].lastClaimedIndex` is updated to `index`, permanently preventing the victim from reclaiming: [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

A victim entitled to `N` tokens receives only `N * (1 - feeInBPS/10000)`. Up to 10% of their claimable yield is permanently redirected to `protocolTreasury` without their consent. The victim cannot reclaim the difference; the state update is irreversible. The attacker needs no capital and bears no risk.

---

### Likelihood Explanation

**Medium.**

Merkle proofs for distributor contracts are routinely published off-chain (IPFS, protocol frontends, subgraphs) so that users can self-claim. Any party with access to the published proof data — which is the normal operational assumption — can execute this attack. The only prerequisite is a non-zero `feeInBPS`, which is a configurable parameter set at initialization and adjustable by the owner.

---

### Recommendation

Add a caller-identity guard at the top of `claim`, mirroring the pattern already used in `KernelMerkleDistributor`:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
+   if (account != msg.sender) revert Unauthorized();
    ...
}
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`.

---

### Proof of Concept

1. Protocol deploys `MerkleDistributor` with `feeInBPS = 1000` (10%).
2. Alice is entitled to `1000e18` tokens; her merkle proof `(index=1, account=Alice, cumulativeAmount=1000e18, proof=[...])` is published off-chain.
3. Bob (attacker, zero capital required) calls:
   ```solidity
   distributor.claim(1, Alice, 1000e18, aliceProof);
   ```
4. The contract verifies the proof, computes `fee = 100e18`, sends `900e18` to Alice and `100e18` to `protocolTreasury`.
5. `userClaims[Alice].lastClaimedIndex = 1` — Alice's claim slot is consumed.
6. Alice attempts to call `claim` herself and receives `AlreadyClaimed`. She has permanently lost `100e18` tokens she was entitled to.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-47)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;
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
