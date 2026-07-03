### Title
Unchecked Caller in `MerkleDistributor.claim` Allows Anyone to Force-Claim on Behalf of Any Account, Permanently Freezing Yield for Smart Contract Recipients - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
The `MerkleDistributor.claim` function accepts an arbitrary `account` parameter and transfers tokens directly to it without verifying that `msg.sender == account`. Any external caller who possesses a valid merkle proof for a target address can trigger that address's claim. When the target is a smart contract that cannot handle ERC20 tokens (e.g., a Uniswap pool or other DeFi protocol holding rsETH), the distributed tokens are permanently locked in that contract.

### Finding Description
`MerkleDistributor.claim` performs merkle proof verification against the supplied `account` address and then unconditionally transfers the claimable amount to `account`:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    // ...
    bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
    if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
        revert InvalidMerkleProof();
    }
    // ...
    IERC20(token).safeTransfer(account, amountToSend);
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

There is no `require(msg.sender == account)` guard. The sibling contract `KernelMerkleDistributor` correctly enforces this restriction inside `_processClaim`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

`MerkleDistributor` omits this check entirely.

Because rsETH is a composable