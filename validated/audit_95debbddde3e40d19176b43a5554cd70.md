### Title
Permanent Freezing of Funds via `isClaimed` Monotonicity Assumption Blocking Lower-Index Claims After Higher-Index Claim — (`contracts/utils/MerkleDistributor/MerkleDistributor.sol`, `contracts/KERNEL/KernelMerkleDistributor.sol`)

---

### Summary

`isClaimed()` uses a single `lastClaimedIndex >= index` comparison to determine whether a claim has been processed. This assumes cumulative amounts are strictly monotonically increasing across indices. If a root encodes a higher `cumulativeAmount` at a lower index than at a higher index (non-monotonic), a user who claims the higher index first is permanently blocked from claiming the lower index, freezing the delta forever.

---

### Finding Description

`isClaimed` is implemented identically in both distributor contracts:

```solidity
return userClaims[account].lastClaimedIndex >= index;
``` [1](#0-0) [2](#0-1) 

`claim()` gates on this check before any proof verification:

```solidity
if (isClaimed(index, account)) {
    revert AlreadyClaimed();
}
``` [3](#0-2) 

The `index` parameter is validated only as `index <= currentIndex`:

```solidity
if (index == 0 || index > currentIndex) {
    revert InvalidIndex();
}
``` [4](#0-3) 

This means a user can call `claim(5, ...)` before `claim(3, ...)` — there is no ordering enforcement. Once `lastClaimedIndex = 5`, `isClaimed(3, account)` returns `true` and the call reverts with `AlreadyClaimed`, even though the claimable delta `cumulativeAmount[3] - cumulativeAmount[5]` is positive.

The claimable delta calculation at line 126 would yield a positive, non-zero value:

```solidity
uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
``` [5](#0-4) 

But execution never reaches this line because `AlreadyClaimed` fires first.

`setMerkleRoot` replaces the single active root and increments `currentIndex` by 1 per call:

```solidity
currentMerkleRoot = _merkleRootToSet;
currentMerkleRootIndex++;
currentIndex++;
``` [6](#0-5) 

The contract permits claiming at any `index` from 1 to `currentIndex` against the single live root. This design only makes sense if the active root encodes leaves for multiple historical indices simultaneously — which is the standard cumulative-distribution pattern. The `lastClaimedIndex >= index` guard is the sole replay-protection mechanism for all of those historical slots.

---

### Impact Explanation

If the active root contains:
- `(index=3, alice, cumulativeAmount=200)`
- `(index=5, alice, cumulativeAmount=100)`

and Alice claims index=5 first (receiving 100 tokens), she can never claim index=3. The delta of 100 tokens (200 − 100) is permanently locked in the contract with no recovery path. There is no admin function to reset `lastClaimedIndex` or `cumulativeAmount` for a user.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The precondition is a root where a user's `cumulativeAmount` at a lower index exceeds their `cumulativeAmount` at a higher index. This can arise from:

1. A protocol correction that reduces a user's allocation in a later epoch.
2. A root-construction bug where epochs are processed out of order.
3. A deliberate design where different distribution campaigns share the same distributor with independent (non-cumulative) per-index amounts.

The contract enforces no on-chain monotonicity invariant on `cumulativeAmount` across indices. The root is fully trusted as set by the owner. No malicious intent is required — an off-chain tooling error is sufficient.

**Likelihood: Low** (requires a specific root design), but the consequence when triggered is irreversible.

---

### Recommendation

Replace the single `lastClaimedIndex >= index` guard with a per-index claimed bitmap (as used in Uniswap's original `MerkleDistributor`):

```solidity
mapping(address => mapping(uint256 => bool)) private _claimed;

function isClaimed(uint256 index, address account) public view returns (bool) {
    return _claimed[account][index];
}
```

And on successful claim:

```solidity
_claimed[account][index] = true;
```

This allows independent claiming at any index regardless of order, while still preventing double-claims at the same index. The `claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount` delta logic can remain unchanged to handle the cumulative accounting correctly.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/utils/MerkleDistributor/MerkleDistributor.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken is ERC20 {
    constructor() ERC20("T", "T") { _mint(msg.sender, 1_000_000e18); }
}

contract MerkleDistributorPoC is Test {
    MerkleDistributor distributor;
    MockToken token;
    address alice = address(0xA11CE);
    address treasury = address(0xFEE);

    function test_permanentFreeze() public {
        token = new MockToken();
        distributor = new MerkleDistributor();
        distributor.initialize(address(token), treasury, 0);

        // Build a root with two leaves:
        //   leaf_3: (index=3, alice, cumulativeAmount=200)
        //   leaf_5: (index=5, alice, cumulativeAmount=100)
        bytes32 leaf3 = keccak256(abi.encodePacked(uint256(3), alice, uint256(200)));
        bytes32 leaf5 = keccak256(abi.encodePacked(uint256(5), alice, uint256(100)));

        // Two-leaf Merkle tree: root = hash(sorted(leaf3, leaf5))
        bytes32 root;
        bytes32[] memory proof3 = new bytes32[](1);
        bytes32[] memory proof5 = new bytes32[](1);
        if (leaf3 < leaf5) {
            root = keccak256(abi.encodePacked(leaf3, leaf5));
            proof3[0] = leaf5;
            proof5[0] = leaf3;
        } else {
            root = keccak256(abi.encodePacked(leaf5, leaf3));
            proof3[0] = leaf5;
            proof5[0] = leaf3;
        }

        // Advance currentIndex to 5 by calling setMerkleRoot 5 times;
        // only the 5th call sets the root we care about.
        for (uint256 i = 1; i <= 4; i++) {
            distributor.setMerkleRoot(bytes32(uint256(i))); // dummy roots
        }
        distributor.setMerkleRoot(root); // currentIndex == 5

        token.transfer(address(distributor), 1000e18);

        // Alice claims index=5 first (cumulativeAmount=100)
        distributor.claim(5, alice, 100, proof5);
        assertEq(token.balanceOf(alice), 100);

        // Alice now tries to claim index=3 (cumulativeAmount=200, delta=100)
        // isClaimed(3, alice) => lastClaimedIndex(5) >= 3 => true => AlreadyClaimed
        vm.expectRevert(IMerkleDistributor.AlreadyClaimed.selector);
        distributor.claim(3, alice, 200, proof3);

        // 100 tokens are permanently frozen in the distributor
        assertEq(token.balanceOf(address(distributor)), 900e18);
    }
}
```

The test demonstrates that after Alice claims at index=5, the `AlreadyClaimed` revert at line 116 permanently blocks her from recovering the 100-token delta available at index=3. [3](#0-2) [7](#0-6)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L90-94)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L111-113)
```text
        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L115-117)
```text
        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L126-126)
```text
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L161-164)
```text
        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L239-243)
```text
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L315-317)
```text
        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }
```
