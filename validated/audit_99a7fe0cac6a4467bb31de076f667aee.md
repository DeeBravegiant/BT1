### Title
Merkle Leaf Nodes Lack Chain ID and Contract Address, Enabling Cross-Chain and Cross-Contract Proof Replay - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol, contracts/KERNEL/KernelMerkleDistributor.sol, contracts/KERNEL/KernelTop100MerkleDistributor.sol)

---

### Summary

The Merkle leaf nodes constructed in `MerkleDistributor`, `KernelMerkleDistributor`, and `KernelTop100MerkleDistributor` do not include `block.chainid` or `address(this)`. This is the direct structural analog of the reported signature-replay bug: a valid Merkle proof generated for one chain or one contract instance is equally valid on any other chain or contract instance that shares the same Merkle root, allowing a claimant to drain allocations beyond their entitlement.

---

### Finding Description

In `MerkleDistributor.sol`, the leaf is constructed as:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) 

In `KernelMerkleDistributor.sol`, the leaf is constructed identically:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [2](#0-1) 

In `KernelTop100MerkleDistributor.sol`, the leaf is:

```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [3](#0-2) 

None of these leaf constructions bind the proof to a specific chain ID or to the address of the verifying contract. The `currentMerkleRoot` is set by the owner and is a plain `bytes32` with no domain separation. [4](#0-3) 

The LRT-rsETH protocol is explicitly multi-chain: it ships `L2/RsETHTokenWrapper.sol`, multiple bridge contracts, and cross-chain rate providers.  If the owner deploys `MerkleDistributor` (or `KernelMerkleDistributor`) on two chains and sets the same Merkle root on both — a natural operational pattern when distributing rewards to holders across chains — a user's proof for chain A is byte-for-byte valid on chain B. The `userClaims` mapping that prevents double-claiming is per-contract storage and provides no cross-chain protection. [5](#0-4) 

The same attack applies across two different contract instances on the same chain if both are loaded with the same Merkle root (e.g., a staging and production deployment, or two distributor rounds that reuse a root).

---

### Impact Explanation

An attacker (any reward claimant) who holds a valid Merkle proof for their allocation on one chain can submit the identical `(index, account, cumulativeAmount, merkleProof)` tuple to every other chain or contract instance that carries the same root. Each instance will independently verify the proof as valid, mark the claim in its own storage, and transfer tokens. The attacker receives `N × allocation` tokens instead of one allocation, draining the distributor balances on all chains beyond the first. This constitutes theft of unclaimed yield (protocol-allocated reward tokens held in the distributor contracts).

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

The LRT-rsETH protocol already operates across Ethereum mainnet and multiple L2s. Distributing the same reward campaign across chains by deploying the same distributor contract with the same Merkle root is a standard operational pattern. No special privilege or leaked key is required; any eligible claimant can execute this by simply submitting their proof on every chain where the contract is deployed. Likelihood is **medium-high** given the multi-chain deployment footprint already present in the repository.

---

### Recommendation

Bind the Merkle leaf to the specific chain and contract by including `block.chainid` and `address(this)` in the leaf preimage. For example, in `MerkleDistributor`:

```solidity
bytes32 node = keccak256(
    abi.encodePacked(block.chainid, address(this), index, account, cumulativeAmount)
);
```

Apply the same fix to `KernelMerkleDistributor._processClaim` and `KernelTop100MerkleDistributor._verifyClaimProof`. Regenerate all Merkle trees off-chain to include these fields. Alternatively, adopt EIP-712 domain separation, which encodes `chainId` and `verifyingContract` by construction.

---

### Proof of Concept

1. Protocol deploys `MerkleDistributor` on Ethereum (address `0xAAA`) and Arbitrum (address `0xBBB`), both initialized with the same `currentMerkleRoot = R`.
2. Alice is allocated 1000 tokens. Her leaf is `keccak256(abi.encodePacked(1, alice, 1000))`. The Merkle proof `P` is valid against root `R`.
3. Alice calls `claim(1, alice, 1000, P)` on Ethereum → receives 1000 tokens. `userClaims[alice]` on Ethereum is updated.
4. Alice calls `claim(1, alice, 1000, P)` on Arbitrum → the proof verifies against the same root `R`, `userClaims[alice]` on Arbitrum is still zero, so `isClaimed` returns false → Alice receives another 1000 tokens.
5. Alice has received 2000 tokens against a 1000-token allocation, stealing 1000 tokens from the Arbitrum distributor balance. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L63-63)
```text
    mapping(address user => UserClaim userClaim) public userClaims;
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L156-167)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-323)
```text
    function _processClaim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        internal
        returns (uint256)
    {
        UtilLib.checkNonZeroAddress(account);

        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L281-298)
```text
    function _verifyClaimProof(address user, uint256 amount, bytes32[] calldata merkleProof) internal view {
        UtilLib.checkNonZeroAddress(user);

        if (merkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (amount == 0) {
            revert ZeroValueProvided();
        }

        // Verify the merkle proof
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);

        if (!isValid) {
            revert InvalidMerkleProof();
        }
```
