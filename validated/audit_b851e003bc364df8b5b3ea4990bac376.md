### Title
Missing Domain Separation in Merkle Leaf Enables Cross-Chain Proof Replay — (`contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

### Summary
`KernelTop100MerkleDistributor._verifyClaimProof` constructs the merkle leaf as `keccak256(abi.encodePacked(user, amount))` with no contract address or chain ID binding. The proof is entirely self-contained: it proves only `(user, amount)` and carries no linkage to the specific deployment context. If the same merkle root is deployed on more than one chain — a realistic scenario for a cross-chain protocol — any user can replay their proof on every chain to claim the full allocation on each, draining KERNEL tokens beyond their entitlement.

### Finding Description
In `_verifyClaimProof` (line 293), the leaf is:

```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [1](#0-0) 

No `address(this)` and no `block.chainid` are mixed into the leaf. The merkle root is fixed at initialization and never updated: [2](#0-1) 

There is no `setMerkleRoot` function in this contract, so the same root is permanent for the lifetime of each deployment. The claim guard only tracks `amountClaimed` against `userTotalClaimableAmount` within a single contract instance: [3](#0-2) 

It does not record which contract or chain the claim was made on. Consequently, a proof `P` that is valid for `(user, amount)` on chain A is equally valid on chain B if the same root is deployed there, because the leaf hash is identical on both chains.

The same structural gap exists in `KernelMerkleDistributor` (leaf: `index, account, cumulativeAmount`) and `MerkleDistributor` (same leaf), neither of which includes `address(this)` or `block.chainid`: [4](#0-3) [5](#0-4) 

### Impact Explanation
**High — Theft of unclaimed yield.**

KERNEL tokens distributed through `KernelTop100MerkleDistributor` are protocol yield/rewards. A user entitled to `X` KERNEL on chain A can replay the identical proof on chain B (and every additional chain where the same root is live) and receive `X` KERNEL per chain. The excess tokens are drawn from the contract's balance, which is funded by the protocol. Other eligible users or the protocol treasury bear the loss.

### Likelihood Explanation
LRT-rsETH is explicitly a multi-chain protocol with L2 pool contracts deployed on Arbitrum, Optimism, Base, and others. Deploying the same KERNEL reward distribution (same merkle root) across multiple chains is a natural operational step. The attacker needs only a valid proof — which they legitimately possess — and knowledge that the same root exists on another chain. No privileged access, no leaked keys, and no front-running is required.

### Recommendation
Include `address(this)` and `block.chainid` in every merkle leaf to bind the proof to a specific contract and chain:

```solidity
// KernelTop100MerkleDistributor._verifyClaimProof
bytes32 leaf = keccak256(
    abi.encodePacked(address(this), block.chainid, user, amount)
);
```

Apply the same fix to `KernelMerkleDistributor._processClaim` and `MerkleDistributor.claim`:

```solidity
bytes32 node = keccak256(
    abi.encodePacked(address(this), block.chainid, index, account, cumulativeAmount)
);
```

Off-chain tree generation must be updated to hash leaves with the same domain prefix.

### Proof of Concept

1. Protocol generates a merkle tree for the Top-100 KERNEL distribution. Alice's leaf: `keccak256(abi.encodePacked(alice, 1000e18))`. Root = `R`.
2. Protocol deploys `KernelTop100MerkleDistributor` on Ethereum mainnet with root `R` and funds it with 100 000 KERNEL.
3. Protocol deploys a second `KernelTop100MerkleDistributor` on Arbitrum with the **same root `R`** and funds it with 100 000 KERNEL.
4. Alice calls `claim(1000e18, proof)` on Ethereum mainnet. Proof verifies: `keccak256(alice, 1000e18)` is in root `R`. Alice receives 1 000 KERNEL (minus fee). `userClaims[alice].amountClaimed = 1000e18`.
5. Alice calls `claim(1000e18, proof)` on Arbitrum with the **identical proof**. The Arbitrum contract has its own independent `userClaims` mapping; `amountClaimed` for Alice is 0. Proof verifies identically because the leaf hash is the same. Alice receives another 1 000 KERNEL.
6. Alice has claimed 2 000 KERNEL instead of 1 000, stealing 1 000 KERNEL from the Arbitrum contract's balance — tokens that belong to other Top-100 recipients or the protocol. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L141-143)
```text
    /// @notice The merkle root
    bytes32 public merkleRoot;

```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L238-241)
```text
        // If user has claimed everything, return 0
        if (userClaim.amountClaimed >= userTotalClaimableAmount) {
            return 0;
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L281-299)
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
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-338)
```text
    function claim(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
        address user = msg.sender;

        // Verify merkle proof and update user claim data
        _verifyClaimProof(user, amount, merkleProof);

        // Get claimable amount
        uint256 claimableAmount = _getUnclaimedVestedAmount(user, amount);

        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim data
        userClaims[user].lastClaimTimestamp = block.timestamp;
        userClaims[user].amountClaimed += claimableAmount;

        // Calculate fee
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        // Transfer tokens
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
        kernel.safeTransfer(user, amountToSend);

        emit Claimed(user, amountToSend);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L319-322)
```text
        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L119-122)
```text
        // Verify the merkle proof.
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
```
