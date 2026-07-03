### Title
Merkle Leaf Hash Missing Chain ID and Contract Address Enables Cross-Chain Proof Replay - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

### Summary
`KernelMerkleDistributor` and `MerkleDistributor` compute Merkle leaf nodes without binding them to the chain ID or the contract address. Because the claimed-status tracking is per-contract, a valid proof used on one chain deployment can be replayed on any other chain deployment that shares the same Merkle root, allowing a user to claim KERNEL tokens multiple times.

### Finding Description
In `KernelMerkleDistributor._processClaim()`, the leaf is constructed as:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) 

The identical pattern appears in `MerkleDistributor.claim()`:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [2](#0-1) 

Neither `block.chainid` nor `address(this)` is included in the hash. The replay-protection check is:

```solidity
return userClaims[account].lastClaimedIndex >= index;
``` [3](#0-2) 

This mapping is local to each contract instance. A claim recorded on Chain A does not affect the `userClaims` state on Chain B. Therefore, if the same Merkle root is set on two deployments — a realistic operational pattern for a multi-chain KERNEL distribution — the same `(index, account, cumulativeAmount, merkleProof)` tuple is accepted on every chain where the root is live.

`KernelTop100MerkleDistributor._verifyClaimProof()` has the same structural flaw with an even simpler leaf:

```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [4](#0-3) 

### Impact Explanation
An attacker (any ordinary KERNEL reward recipient) can claim their full allocation on every chain where the same root is deployed, draining KERNEL tokens that belong to other claimants or to the protocol treasury. This is direct theft of unclaimed yield.

**Impact: High — Theft of unclaimed yield.**

### Likelihood Explanation
Kelp DAO already operates across Ethereum mainnet, Arbitrum, Optimism, Base, and other L2s. Deploying a KERNEL reward campaign with a single shared Merkle root across chains is a natural operational choice (one snapshot, one root, claim on any chain). The leaf encoding gives no on-chain signal that this is unsafe, so the protocol team has no automatic guard against it. No private key compromise or governance capture is required — only the routine act of setting the same root on two deployments.

**Likelihood: Medium.**

### Recommendation
Bind every leaf to the specific deployment by including `block.chainid` and `address(this)` in the hash:

```solidity
bytes32 node = keccak256(
    abi.encodePacked(block.chainid, address(this), index, account, cumulativeAmount)
);
```

Apply the same fix to `MerkleDistributor` and `KernelTop100MerkleDistributor`. This ensures a proof generated for one chain and one contract address is cryptographically invalid on any other deployment, mirroring the resolution described in the external report (adding `block.chainid` and the contract address to the signed hash).

### Proof of Concept

1. Kelp DAO deploys `KernelMerkleDistributor` on Ethereum mainnet (`0xAAA...`) and Arbitrum (`0xBBB...`), calling `setMerkleRoot(root)` with the **same** `root` on both.
2. Alice holds a valid proof: `(index=5, account=Alice, cumulativeAmount=1000e18, proof=[...])`.
3. Alice calls `claim(5, Alice, 1000e18, proof)` on mainnet → succeeds; `userClaims[Alice].lastClaimedIndex = 5` on mainnet only.
4. Alice calls `claim(5, Alice, 1000e18, proof)` on Arbitrum → `userClaims[Alice].lastClaimedIndex` is `0` on Arbitrum; `isClaimed` returns `false`; the proof verifies against the same root; Alice receives another 1000e18 KERNEL.
5. Alice repeats on every additional chain where the root is deployed, stealing KERNEL tokens that should belong to other claimants. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L242-243)
```text
        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-346)
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

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim info
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
    }
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L293-294)
```text
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);
```
