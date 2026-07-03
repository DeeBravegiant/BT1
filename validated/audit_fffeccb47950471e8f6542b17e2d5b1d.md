Audit Report

## Title
`KernelMerkleDistributor._processClaim()` Restricts Claiming to Beneficiary Account Only - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

## Summary
`KernelMerkleDistributor._processClaim()` contains an `account != msg.sender` guard at line 311 that prevents any address other than the beneficiary from submitting a valid merkle proof on their behalf. The public interface accepts an explicit `account` parameter — the standard pattern for permissionless, proof-gated claiming — but the guard silently negates that design. The sister contract `MerkleDistributor.sol` implements the identical interface without this restriction, confirming the check is erroneous.

## Finding Description
Both public entry points `claim()` and `claimAndStake()` accept an `account` parameter and delegate to `_processClaim()`: [1](#0-0) [2](#0-1) 

Inside `_processClaim()`, after basic sanity checks, the following guard unconditionally reverts if the caller is not the beneficiary: [3](#0-2) 

The merkle proof already cryptographically binds `index`, `account`, and `cumulativeAmount` together — a valid proof can only be constructed for the exact `account` encoded in the tree, making the `msg.sender` check redundant and harmful: [4](#0-3) 

The reference implementation `MerkleDistributor.sol` implements the same `IMerkleDistributor` interface and has no such restriction, allowing any caller to submit a proof on behalf of any `account`: [5](#0-4) 

**Exploit path:**
1. Protocol allocates KERNEL rewards to `account = 0xCONTRACT` (e.g., a vault or multisig with no direct `execute` path) in the merkle tree.
2. `0xCONTRACT` cannot originate a call to `claim()` directly.
3. A keeper/relayer calls `claim(index, 0xCONTRACT, amount, proof)` from their EOA.
4. `_processClaim` evaluates `account (0xCONTRACT) != msg.sender (keeper EOA)` → `revert Unauthorized()`.
5. KERNEL tokens allocated to `0xCONTRACT` remain permanently locked in the distributor.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

Any beneficiary address that cannot directly originate an on-chain transaction to `KernelMerkleDistributor` — smart-contract wallets without a generic `execute` path, deprecated multisigs, vault contracts — is permanently unable to claim their KERNEL allocation. Tokens are not stolen; they remain in the distributor. The contract fails to deliver the promised KERNEL rewards to a realistic class of beneficiaries.

## Likelihood Explanation
**Medium.** Merkle-based reward distributions routinely include smart-contract addresses (protocol treasuries, multisigs, staking vaults). The `account` parameter in the public interface explicitly signals that third-party submission is intended. Any such beneficiary that cannot self-call will trigger the revert without any attacker involvement — it is a passive, always-on failure condition requiring no special setup.

## Recommendation
Remove the `account != msg.sender` guard from `_processClaim`. The merkle proof is sufficient authorization:

```diff
-        if (account != msg.sender) {
-            revert Unauthorized();
-        }
-
         if (isClaimed(index, account)) {
``` [6](#0-5) 

## Proof of Concept
**Foundry test plan:**
```solidity
function test_thirdPartyCannotClaimOnBehalf() public {
    // Setup: deploy KernelMerkleDistributor, build merkle tree with
    // account = address(contractWallet), set merkle root
    address contractWallet = address(new MockContractNoExecute());
    bytes32[] memory proof = buildProof(index, contractWallet, amount);

    // Keeper (EOA) attempts to claim on behalf of contractWallet
    vm.prank(keeper);
    vm.expectRevert(IMerkleDistributor.Unauthorized.selector);
    distributor.claim(index, contractWallet, amount, proof);

    // Confirm contractWallet itself also cannot claim (no execute path)
    // → tokens permanently locked
    assertEq(kernel.balanceOf(contractWallet), 0);
}
```

The test demonstrates that a keeper holding a valid merkle proof for `contractWallet` is blocked by the `account != msg.sender` guard, and since `contractWallet` has no `execute` path, the KERNEL allocation is permanently inaccessible.

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-317)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L320-323)
```text
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-123)
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
```
