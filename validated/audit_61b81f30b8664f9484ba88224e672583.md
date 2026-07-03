### Title
Unauthorized Claim on Behalf of Any Address Enables Permanent Freezing of Reward Tokens - (File: `contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim()` allows any unprivileged caller to trigger a token transfer to an arbitrary `account` address without verifying that `msg.sender == account`. If the target `account` is a contract that cannot handle or forward unexpected ERC20 token receipts, the claimed reward tokens are permanently frozen.

---

### Finding Description

The `claim()` function in `MerkleDistributor` accepts an `account` parameter and transfers the claimable ERC20 reward tokens directly to that address:

```solidity
function claim(
    uint256 index,
    address account,        // caller-supplied, no msg.sender check
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
)
    external
    override
    whenNotPaused
{
    ...
    IERC20(token).safeTransfer(account, amountToSend);   // line 141
    IERC20(token).safeTransfer(protocolTreasury, fee);   // line 144
    ...
}
```

There is no check that `msg.sender == account`. The only validation performed is that the merkle proof is valid for the `(index, account, cumulativeAmount)` tuple — which is public, off-chain data. Any external caller can supply a valid proof for any eligible `account` and force a token transfer to that address.

Compare this to `KernelMerkleDistributor._processClaim()`, which explicitly guards against this:

```solidity
if (account != msg.sender) {
    revert Unauthorized();   // line 311-313
}
```

`MerkleDistributor` has no equivalent guard.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract address is listed as an eligible `account` in the merkle tree (e.g., a multisig, a vault, a DeFi integration, or any contract that calls `MerkleDistributor.claim()` internally and expects to receive tokens only when it initiates the call), an attacker can front-run or independently call `claim()` on its behalf. The ERC20 reward tokens are transferred to the contract. If that contract has no rescue function and no logic to handle an unexpected token receipt, the tokens are permanently locked.

Additionally, the claim state for `account` is marked as consumed (`userClaims[account].lastClaimedIndex = index`), so the legitimate owner can never re-claim — the loss is irreversible.

---

### Likelihood Explanation

**Medium.** The merkle tree data (index, account, cumulativeAmount, proof) is public off-chain data. Any attacker can read it and call `claim()` for any contract address in the tree. No special privilege or access is required. The attacker only needs to identify a contract `account` that cannot safely receive unexpected ERC20 transfers. This is a realistic scenario for any protocol that integrates with `MerkleDistributor` and holds an eligible address in the merkle tree.

---

### Recommendation

Add a caller authorization check inside `claim()`, mirroring the fix already present in `KernelMerkleDistributor`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Alternatively, remove the `account` parameter entirely and derive it from `msg.sender`, as done in `KernelTop100MerkleDistributor.claim()`.

---

### Proof of Concept

1. The merkle tree contains a leaf for `(index=5, account=VaultContract, cumulativeAmount=1000e18)`.
2. `VaultContract` is a contract that does not implement any ERC20 rescue logic and does not expect to receive tokens unless it calls `claim()` itself.
3. Attacker calls:
   ```solidity
   merkleDistributor.claim(5, address(VaultContract), 1000e18, validProof);
   ```
4. `MerkleDistributor` verifies the proof (valid), marks `userClaims[VaultContract].lastClaimedIndex = 5`, and executes:
   ```solidity
   IERC20(token).safeTransfer(address(VaultContract), amountToSend);
   ```
5. Tokens arrive at `VaultContract` with no handler. They are permanently frozen.
6. `VaultContract` later attempts to call `claim()` itself — it reverts with `AlreadyClaimed` because the state was already consumed in step 4.

**Root cause:** [1](#0-0)  — no `msg.sender == account` check anywhere in the function, while the token transfer at line 141 sends directly to the caller-supplied `account`.

**Contrast with the patched sibling contract:** [2](#0-1)  — `KernelMerkleDistributor` explicitly reverts if `account != msg.sender`.

### Citations

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
