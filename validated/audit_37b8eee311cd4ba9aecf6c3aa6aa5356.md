### Title
Missing Chain ID and Contract Address in Merkle Leaf Encoding Enables Cross-Chain Replay of Token Claims - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.claim()` encodes the merkle leaf without including `block.chainid` or `address(this)`. Combined with the fact that `account` is a caller-supplied parameter (not `msg.sender`), any third party can replay a valid claim transaction from one chain on any other chain where the same contract is deployed with the same merkle root, draining the distributor of tokens intended for other users.

### Finding Description
In `MerkleDistributor.claim()`, the leaf node is computed as:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
```

Neither `block.chainid` nor `address(this)` is included in the encoding. This means a proof that is valid on chain A is equally valid on chain B if the same merkle root is set. The function signature accepts `account` as an arbitrary caller-supplied parameter rather than enforcing `msg.sender == account`, so any external caller can submit a claim on behalf of any address. [1](#0-0) 

Compare this to `KernelMerkleDistributor._processClaim()`, which at least enforces `account == msg.sender` (line 311), partially mitigating the "anyone can trigger" vector, though it still lacks chain ID in its own leaf encoding: [2](#0-1) 

`MerkleDistributor` has no such restriction: [3](#0-2) 

The same pattern exists in `KernelTop100MerkleDistributor._verifyClaimProof()`, where the leaf is `keccak256(abi.encodePacked(user, amount))` with no chain ID: [4](#0-3) 

### Impact Explanation
**High — Theft of unclaimed yield.**

If `MerkleDistributor` is deployed on multiple chains (Ethereum mainnet, an L2, or a fork) with the same merkle root — a realistic scenario given the protocol's explicit cross-chain infrastructure — an attacker can:

1. Observe a successful `claim(index, account, cumulativeAmount, proof)` on chain A.
2. Replay the identical calldata on chain B.
3. The distributor on chain B transfers tokens to `account`, which may be a smart-contract wallet or exchange deposit address that does not exist on chain B, permanently locking those tokens.
4. Repeated replays drain the distributor on chain B before legitimate users on that chain can claim, stealing their allocated yield.

Because `account` is a free parameter, no signature or `msg.sender` check prevents the replay. Tokens transferred to non-existent contract addresses on the target chain are permanently frozen. [5](#0-4) 

### Likelihood Explanation
The LRT-rsETH repository contains dedicated cross-chain infrastructure (`CrossChainRateProvider`, `RSETHMultiChainRateProvider`, bridges, CCIP contracts), making multi-chain deployment of distributor contracts a concrete operational expectation rather than a hypothetical.



An attacker needs only to monitor the public mempool or confirmed transactions on one chain and replay them on another — no privileged access, no key compromise, and no brute force required.

### Recommendation
1. Include `block.chainid` and `address(this)` in the leaf encoding:
   ```solidity
   bytes32 node = keccak256(
       abi.encodePacked(block.chainid, address(this), index, account, cumulativeAmount)
   );
   ```
2. Enforce `require(account == msg.sender, "Unauthorized")` in `MerkleDistributor.claim()`, consistent with the guard already present in `KernelMerkleDistributor._processClaim()`.
3. Apply the same fix to `KernelTop100MerkleDistributor._verifyClaimProof()` and `MerkleBlastPointsDistributor.claim()`.

### Proof of Concept
1. Protocol deploys `MerkleDistributor` on Ethereum (chain 1) and Arbitrum (chain 42161) with identical `currentMerkleRoot`.
2. Alice calls `claim(1, alice, 1000e18, proof)` on Ethereum; transaction succeeds.
3. Attacker copies the calldata and submits `claim(1, alice, 1000e18, proof)` on Arbitrum.
4. `keccak256(abi.encodePacked(1, alice, 1000e18))` produces the same leaf on Arbitrum; proof verifies against the same root.
5. `alice` has `lastClaimedIndex == 0` on Arbitrum (state is independent per chain), so `isClaimed` returns `false`.
6. Arbitrum distributor transfers 1000e18 tokens to `alice`. If `alice` is a Gnosis Safe deployed only on Ethereum, the tokens are sent to a bare EOA-equivalent address on Arbitrum and may be unrecoverable.
7. Attacker repeats for every leaf in the tree, draining the Arbitrum distributor entirely. [6](#0-5)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L89-147)
```text
    /// @inheritdoc IMerkleDistributor
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }

    /// @inheritdoc IMerkleDistributor
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-321)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L292-294)
```text
        // Verify the merkle proof
        bytes32 leaf = keccak256(abi.encodePacked(user, amount));
        bool isValid = MerkleProofUpgradeable.verify(merkleProof, merkleRoot, leaf);
```
