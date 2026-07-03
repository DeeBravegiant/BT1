Audit Report

## Title
`_processClaim` unconditionally reverts for `account != msg.sender`, permanently freezing unclaimed KERNEL yield for smart-contract beneficiaries - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

## Summary
`KernelMerkleDistributor.claim` and `claimAndStake` both accept a caller-supplied `account` parameter, implying the ability to claim on behalf of a merkle-leaf address. However, the shared internal helper `_processClaim` unconditionally reverts when `account != msg.sender`. Any merkle-leaf address that is a smart contract incapable of directly encoding and dispatching the `claim` call (e.g., a simple vault, a token contract, a deprecated proxy) has its KERNEL allocation permanently locked in the distributor with no alternative recovery path.

## Finding Description
Both public entry points delegate to `_processClaim`: [1](#0-0) 

This check is applied before the merkle proof is verified. The merkle leaf is constructed as: [2](#0-1) 

The `(index, account, cumulativeAmount)` tuple is fixed at snapshot time by the protocol. The merkle proof already cryptographically binds the caller-supplied `account` to the leaf; the additional `account != msg.sender` guard is redundant for security but harmful for usability. Any smart contract address appearing in the tree that lacks a general `execute` mechanism (e.g., a simple vault, a liquidity pool, a token contract, or any contract without a dedicated `claim` wrapper) cannot satisfy `msg.sender == account` and is permanently excluded from claiming. No approved operator, relayer, or third party can substitute. The public function signatures: [3](#0-2) [4](#0-3) 

advertise `account` as a parameter (implying delegation), but the implementation silently collapses this to a strict self-only check, creating a misleading API that causes permanent loss of yield for affected beneficiaries.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** KERNEL tokens allocated to a merkle leaf whose `account` is a smart contract that cannot directly call `claim` are locked in the distributor indefinitely. The tokens remain in the contract but are irrecoverable by the rightful beneficiary. This matches the allowed impact category "Medium. Permanent freezing of unclaimed yield."

## Likelihood Explanation
Merkle distributions in DeFi routinely include smart-contract addresses. While multisigs and DAO governors can often encode arbitrary calls and satisfy `msg.sender == account`, simpler contracts (vaults, pools, deprecated proxies, token contracts) frequently cannot. Any such address appearing in a distribution snapshot triggers the freeze. The condition is reachable by any unprivileged external caller who passes `account != msg.sender`, and no privileged action is required to trigger it.

## Recommendation
Remove the `account != msg.sender` guard from `_processClaim`. The merkle proof already cryptographically binds `(index, account, cumulativeAmount)`; no additional caller-identity check is needed. Tokens are always transferred to `account`, so no third party can redirect funds:

```solidity
// Remove:
// if (account != msg.sender) {
//     revert Unauthorized();
// }
```

If self-only claiming is intentional, remove the `account` parameter from the public ABI entirely and replace it with `msg.sender` throughout, making the restriction explicit and eliminating the misleading signature.

## Proof of Concept
1. Protocol snapshots a merkle tree including `account = 0xVault` (a simple vault contract with no `execute` function) with `cumulativeAmount = 1000e18`.
2. Any caller attempts:
   ```solidity
   kernelMerkleDistributor.claim(index, 0xVault, 1000e18, proof);
   ```
3. `_processClaim` executes `if (0xVault != msg.sender)` → `revert Unauthorized()`.
4. `0xVault` itself cannot call `claim` because it has no mechanism to dispatch arbitrary external calls.
5. `claimAndStake` shares the same `_processClaim` gate — no alternative entry point exists.
6. The 1000e18 KERNEL tokens remain locked in the distributor permanently.

**Foundry test sketch:**
```solidity
function test_smartContractAccountFrozen() public {
    // Deploy a minimal contract with no execute function as the beneficiary
    SimpleVault vault = new SimpleVault();
    // Build merkle tree with vault as leaf account
    // ...
    vm.prank(address(this)); // relayer, not vault
    vm.expectRevert(IMerkleDistributor.Unauthorized.selector);
    distributor.claim(index, address(vault), 1000e18, proof);
    // vault itself cannot call claim — tokens are permanently frozen
}
```

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L250-265)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        nonReentrant
        whenNotPaused
    {
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);

        emit Claimed(index, account, amountToSend);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L270-285)
```text
    function claimAndStake(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        nonReentrant
        whenNotPaused
    {
        uint256 amountToStake = _processClaim(index, account, cumulativeAmount, merkleProof);

        IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake);

        emit ClaimedAndStaked(index, account, amountToStake);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L320-321)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
```
