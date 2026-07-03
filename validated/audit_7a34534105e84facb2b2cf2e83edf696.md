### Title
Missing Domain Separator in Merkle Leaf Construction Enables Cross-Chain Proof Replay — (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

The Merkle leaf hash in `MerkleDistributor`, `KernelMerkleDistributor`, and `KernelTop100MerkleDistributor` omits any domain separator (contract address or chain ID). This is the on-chain analog of supporting deprecated TLS versions: the cryptographic binding is weak, so a valid proof from one deployment can be replayed verbatim on any other deployment that shares the same Merkle root, enabling theft of unclaimed yield from other users.

---

### Finding Description

In `MerkleDistributor.sol`, the leaf is constructed as:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) 

In `KernelMerkleDistributor.sol`, identically:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [2](#0-1) 

In `KernelTop100MerkleDistributor.sol`:

```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [3](#0-2) 

None of these leaf hashes include `address(this)` (the contract address) or `block.chainid`. Because the leaf is purely a function of `(index, account, amount)`, a proof that is valid on one chain is equally valid on any other chain where the same Merkle root is set. The protocol is explicitly multi-chain (Arbitrum, Optimism, Base, etc.), and `MerkleDistributor` is a generic, reusable contract designed to be deployed across chains.

The `isClaimed` guard only tracks state per-chain:

```solidity
return userClaims[account].lastClaimedIndex >= index;
``` [4](#0-3) 

State on Chain B is entirely independent of Chain A, so a claim already processed on Chain A does not prevent replay on Chain B.

Additionally, `MerkleDistributor.claim()` does not enforce `account == msg.sender`: [5](#0-4) 

This contrasts with `KernelMerkleDistributor`, which does enforce it: [6](#0-5) 

The weaker binding in `MerkleDistributor` is the direct analog of a server advertising TLS 1.0/1.1 alongside TLS 1.3: the weaker scheme is available and exploitable.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

If the same Merkle root is set on two chains (Chain A and Chain B), a user with a valid proof for `(index, account, amount)` on Chain A can submit the identical proof on Chain B. Their `userClaims` on Chain B starts at zero, so the replay passes all guards. The user receives tokens on Chain B that were budgeted for other users on Chain B. Those other users are then unable to claim (contract balance depleted), constituting theft of their unclaimed yield.

---

### Likelihood Explanation

**Low-Medium.** The protocol is explicitly multi-chain. `MerkleDistributor` is a generic utility contract intended for reuse. A multi-chain token distribution (e.g., KERNEL rewards across Arbitrum and Optimism) is a realistic operational scenario. An operator setting the same Merkle root on multiple chains — without realizing the replay implication — is a plausible mistake, not an exotic edge case. The Merkle proofs are published off-chain (IPFS / protocol UI), making them trivially accessible to any attacker.

---

### Recommendation

Include `block.chainid` and `address(this)` in every leaf hash to bind the proof to a specific contract instance on a specific chain:

```solidity
// MerkleDistributor
bytes32 node = keccak256(
    abi.encodePacked(block.chainid, address(this), index, account, cumulativeAmount)
);

// KernelTop100MerkleDistributor
bytes32 leaf = keccak256(
    abi.encodePacked(block.chainid, address(this), user, amount)
);
```

This is the standard practice recommended by OpenZeppelin's JavaScript Merkle-tree library and mirrors EIP-712's domain separator, which exists precisely to prevent cross-chain and cross-contract replay.

---

### Proof of Concept

1. Protocol deploys `MerkleDistributor` on Arbitrum (Chain A) and Optimism (Chain B), both initialized with the same `currentMerkleRoot` (e.g., for a multi-chain KERNEL airdrop).
2. Attacker (Alice) obtains her valid proof for `(index=1, account=Alice, cumulativeAmount=1000e18)` from the public distribution endpoint.
3. Alice calls `claim(1, Alice, 1000e18, proof)` on Chain A → receives `1000e18 - fee` tokens. ✓
4. Alice calls `claim(1, Alice, 1000e18, proof)` on Chain B → `userClaims[Alice]` on Chain B is zero; all guards pass; Alice receives another `1000e18 - fee` tokens from Chain B's budget. ✓
5. Bob, a legitimate Chain B recipient, calls `claim(1, Bob, 500e18, bobProof)` → the contract has insufficient balance; Bob's claim reverts. Bob's unclaimed yield is permanently frozen.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L90-93)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L319-322)
```text
        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L293-294)
```text
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);
```
