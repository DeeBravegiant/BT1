Audit Report

## Title
Unbounded `merkleProof` Array Allows Arbitrary Gas Consumption in `claim()` / `claimAndStake()` - (File: `contracts/KERNEL/KernelTop100MerkleDistributor.sol`)

## Summary
`_verifyClaimProof` imposes no upper bound on `merkleProof.length` before passing it to `MerkleProofUpgradeable.verify`, which iterates once per element computing a `keccak256` per iteration. Any unprivileged caller can submit an arbitrarily large proof array to `claim()` or `claimAndStake()`, forcing O(N) hashing work before the call reverts with `InvalidMerkleProof`. Gas consumption scales linearly with proof length up to the block gas limit, enabling block-stuffing at attacker-controlled cost.

## Finding Description
`claim()` [1](#0-0)  and `claimAndStake()` [2](#0-1)  both delegate proof validation to `_verifyClaimProof`. That function performs no length check on the `merkleProof` array before forwarding it to `MerkleProofUpgradeable.verify`: [3](#0-2) 

The OZ library's `processProof` is an unbounded loop — one `keccak256` per element — so gas cost is strictly proportional to `merkleProof.length`. The call always reverts with `InvalidMerkleProof` for a fabricated proof, meaning no state is written and no funds move, but all gas up to the block limit is consumed. Because the contract is explicitly scoped to 100 users, a legitimate proof depth is at most ⌈log₂(100)⌉ = 7 elements; there is no guard enforcing this invariant.

## Impact Explanation
**Medium — Unbounded gas consumption / Low — Block stuffing.** An attacker submitting a proof of ~900,000 elements can approach the 30 M block gas limit in a single transaction. Repeated submissions across consecutive blocks can crowd out legitimate `claim()` calls from being included, delaying or temporarily preventing eligible users from accessing their vested KERNEL tokens. No funds are permanently lost, placing this squarely in the Medium (unbounded gas) and Low (block stuffing) impact tiers explicitly listed in scope.

## Likelihood Explanation
The entrypoints `claim()` and `claimAndStake()` are fully public with no role check. [4](#0-3)  The only cost to the attacker is the gas fee. On any L2 or low-fee network the cost of sustained block-stuffing is negligible. The attack requires no privileged access, no victim interaction, and no external protocol dependency — any EOA can execute it at will.

## Recommendation
Add an explicit maximum proof depth constant and check at the top of `_verifyClaimProof`:

```solidity
uint256 private constant MAX_PROOF_LENGTH = 8; // ceil(log2(100)) + 1

function _verifyClaimProof(
    address user,
    uint256 amount,
    bytes32[] calldata merkleProof
) internal view {
    if (merkleProof.length > MAX_PROOF_LENGTH) revert InvalidMerkleProof();
    // ... existing checks
}
```

This bounds gas to O(log N) as the design invariant requires and eliminates the attack surface entirely.

## Proof of Concept
Deploy `KernelTop100MerkleDistributor` with a valid non-zero `merkleRoot` and a future `vestingStartTimestamp`. Run the following Foundry fuzz test:

```solidity
function testFuzz_unboundedGas(uint16 proofLen) public {
    proofLen = uint16(bound(proofLen, 1, 10_000));
    bytes32[] memory fakeProof = new bytes32[](proofLen);
    for (uint256 i = 0; i < proofLen; i++) {
        fakeProof[i] = keccak256(abi.encode(i));
    }
    uint256 gasBefore = gasleft();
    try distributor.claim(1 ether, fakeProof) {} catch {}
    uint256 gasUsed = gasBefore - gasleft();
    emit log_named_uint("proofLen", proofLen);
    emit log_named_uint("gasUsed",  gasUsed);
}
```

`gasUsed` will grow linearly with `proofLen` with no enforced ceiling, confirming the vulnerability. [5](#0-4)

### Citations

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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-314)
```text
    function claim(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
        address user = msg.sender;

        // Verify merkle proof and update user claim data
        _verifyClaimProof(user, amount, merkleProof);
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L345-349)
```text
    function claimAndStake(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
        address user = msg.sender;

        // Verify merkle proof and update user claim data
        _verifyClaimProof(user, amount, merkleProof);
```
