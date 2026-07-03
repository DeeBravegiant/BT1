Audit Report

## Title
Merkle Leaf Nodes Lack Chain ID and Contract Address, Enabling Cross-Chain Proof Replay — (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol, contracts/KERNEL/KernelMerkleDistributor.sol, contracts/KERNEL/KernelTop100MerkleDistributor.sol)

## Summary
All three distributor contracts construct Merkle leaf nodes without binding them to `block.chainid` or `address(this)`. Because the protocol operates across Ethereum mainnet and multiple L2s (evidenced by `contracts/L2/RsETHTokenWrapper.sol` and eleven bridge contracts under `contracts/bridges/`), deploying the same distributor with the same Merkle root on multiple chains is a natural operational pattern. Any eligible claimant can replay their proof on every chain that carries the same root, receiving `N × allocation` tokens instead of one.

## Finding Description
In `MerkleDistributor.sol` the leaf is:
```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [1](#0-0) 

`KernelMerkleDistributor._processClaim` constructs an identical leaf:
```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [2](#0-1) 

`KernelTop100MerkleDistributor._verifyClaimProof` constructs:
```solidity
bytes32 leaf = keccak256(abi.encodePacked(user, amount));
``` [3](#0-2) 

None of these include `block.chainid` or `address(this)`. The `currentMerkleRoot` / `merkleRoot` is a plain `bytes32` set by the owner with no domain separation. [4](#0-3) 

The double-claim guard (`userClaims` / `isClaimed`) is per-contract storage and provides no cross-chain protection. [5](#0-4) 

**Exploit path:**
1. Owner deploys `MerkleDistributor` on Ethereum and Arbitrum, both initialized with the same `currentMerkleRoot = R` (natural for a multi-chain reward campaign).
2. Alice holds a valid proof `P` for leaf `keccak256(abi.encodePacked(1, alice, 1000))` against root `R`.
3. Alice calls `claim(1, alice, 1000, P)` on Ethereum → proof verifies, `userClaims[alice]` on Ethereum updated, 1000 tokens transferred.
4. Alice calls `claim(1, alice, 1000, P)` on Arbitrum → proof verifies against the same root `R`, `userClaims[alice]` on Arbitrum is still zero, `isClaimed` returns false → another 1000 tokens transferred.
5. Alice receives 2000 tokens against a 1000-token allocation.

The same replay applies across two contract instances on the same chain if both carry the same root (e.g., two distribution rounds that reuse a root, or a staging and production deployment). [6](#0-5) 

## Impact Explanation
**High — Theft of unclaimed yield.** Any claimant can drain distributor balances on all chains beyond the first by replaying their proof. The stolen tokens are reward tokens held in the distributor contracts, which maps exactly to the allowed impact "Theft of unclaimed yield." The attack requires no special privilege; any eligible claimant can execute it unilaterally. [7](#0-6) 

## Likelihood Explanation
The repository already contains `contracts/L2/RsETHTokenWrapper.sol` and eleven bridge contracts (`ArbitrumMessenger`, `OptimismMessenger`, `LineaMessenger`, `ScrollMessenger`, `UnichainMessenger`, `SonicBridgeReceiver`, etc.), confirming active multi-chain deployment.  Distributing the same reward campaign across chains by deploying the same distributor with the same Merkle root is a standard operational pattern requiring no attacker privilege. Every eligible claimant is a potential attacker, and the attack is repeatable across every chain where the root is deployed.

## Recommendation
Bind the leaf to the specific chain and contract by including `block.chainid` and `address(this)` in the leaf preimage. For `MerkleDistributor` and `KernelMerkleDistributor`:
```solidity
bytes32 node = keccak256(
    abi.encodePacked(block.chainid, address(this), index, account, cumulativeAmount)
);
```
For `KernelTop100MerkleDistributor._verifyClaimProof`:
```solidity
bytes32 leaf = keccak256(abi.encodePacked(block.chainid, address(this), user, amount));
```
Regenerate all Merkle trees off-chain to include these fields. Alternatively, adopt EIP-712 domain separation, which encodes `chainId` and `verifyingContract` by construction.

## Proof of Concept
**Foundry fork test outline:**
1. Fork Ethereum mainnet and Arbitrum mainnet in the same test using `vm.createSelectFork`.
2. Deploy `MerkleDistributor` on both forks; call `setMerkleRoot(R)` on both with the same root.
3. Fund both contracts with 1000 tokens each.
4. Construct Alice's leaf `keccak256(abi.encodePacked(1, alice, 1000))` and generate proof `P` off-chain.
5. On the Ethereum fork: `vm.prank(alice); distributor.claim(1, alice, 1000, P)` → assert Alice's balance increases by 1000.
6. Switch to Arbitrum fork: `vm.prank(alice); distributorArb.claim(1, alice, 1000, P)` → assert the call succeeds (proof still valid, `userClaims[alice]` is zero on this fork) and Alice's balance increases by another 1000.
7. Assert total received = 2000 against a 1000-token allocation, confirming the replay.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L63-63)
```text
    mapping(address user => UserClaim userClaim) public userClaims;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L120-120)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
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
