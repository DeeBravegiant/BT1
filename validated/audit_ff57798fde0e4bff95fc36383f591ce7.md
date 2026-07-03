Looking at the full `HashStorage.sol` implementation, I can now analyze this precisely.

### Title
Unbounded O(N) Duplicate-Scan Loop in `addWithdrawal` Can Permanently Brick the L2-to-L1 Withdrawal Pipeline — (`contracts/utils/HashStorage.sol`)

---

### Summary

`HashStorage.addWithdrawal` performs a full linear scan of the `withdrawalTxHashes` storage array on every call to detect duplicates. Because the array has no size cap and shrinks only when `setWithdrawalClaimed` is called, a sustained backlog of unclaimed L2-to-L1 withdrawal hashes can grow the array to a length where the loop's cumulative cold-SLOAD cost exceeds Ethereum's block gas limit (~30 M gas), permanently preventing any further hash registration.

---

### Finding Description

`addWithdrawal` iterates over every element of `withdrawalTxHashes` before appending a new hash: [1](#0-0) 

Each `withdrawalTxHashes[i]` read is a distinct storage slot (dynamic-array elements are stored at `keccak256(slot) + i`). Under EIP-2929, the first access to each slot within a transaction costs **2 100 gas** (cold SLOAD). At Ethereum's ~30 M gas block limit:

```
30_000_000 / 2_100 ≈ 14_285 cold SLOADs
```

So once `withdrawalTxHashes.length` reaches roughly **14 000–15 000**, the loop alone exhausts the block gas limit and every subsequent `addWithdrawal` call reverts with out-of-gas.

The array grows by 1 on every `addWithdrawal` call and shrinks by 1 only when `setWithdrawalClaimed` is called: [2](#0-1) [3](#0-2) 

There is no cap on the array length and no alternative O(1) path for duplicate detection. The `claimedWithdrawals` mapping tracks only *already-claimed* hashes (set to `true` after `setWithdrawalClaimed`), so it cannot detect duplicates among *pending* hashes: [4](#0-3) [5](#0-4) 

The same O(N) loop exists in `setWithdrawalClaimed` (lines 114–121), meaning that once the array is large enough, **neither** function can execute.

---

### Impact Explanation

Once the array crosses the gas-limit threshold, `addWithdrawal` permanently reverts. No new L2-to-L1 withdrawal hash can ever be registered. Any L2 withdrawal that has not yet been recorded on L1 is frozen in limbo: the operator cannot register it, and users cannot claim it. This constitutes a permanent freezing of the L2-to-L1 withdrawal pipeline.

**Impact: Low — Block stuffing / contract fails to deliver promised returns.**

---

### Likelihood Explanation

The operator must accumulate ~14 000 unclaimed hashes. This requires a sustained period where `addWithdrawal` is called far more frequently than `setWithdrawalClaimed`. In a high-throughput L2 environment with delayed or batched claiming, this is operationally plausible over time. No adversarial action is required — ordinary operational backlog is sufficient.

---

### Recommendation

Replace the O(N) array scan with an O(1) mapping lookup. Add a `pendingWithdrawals` mapping that is set to `true` on `addWithdrawal` and cleared on `setWithdrawalClaimed`:

```solidity
mapping(bytes32 txHash => bool isPending) public pendingWithdrawals;

function addWithdrawal(bytes32 txHash) external onlyRole(OPERATOR_ROLE) {
    if (txHash == bytes32(0)) revert InvalidHash();
    if (claimedWithdrawals[txHash]) revert HashAlreadyClaimed();
    if (pendingWithdrawals[txHash]) revert DuplicateHash();   // O(1)

    pendingWithdrawals[txHash] = true;
    withdrawalTxHashes.push(txHash);
    emit WithdrawalAdded(txHash);
}
```

Apply the same fix to `setWithdrawalClaimed` — use the mapping to verify existence instead of the loop, and maintain a separate index mapping if swap-and-pop is still desired.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/utils/HashStorage.sol";

contract HashStorageGasTest is Test {
    HashStorage hs;
    address operator = address(0xBEEF);

    function setUp() public {
        hs = new HashStorage(address(this), operator, "test");
    }

    function test_addWithdrawal_gasGrowth() public {
        uint256 N = 15_000;
        vm.startPrank(operator);

        for (uint256 i = 1; i <= N; i++) {
            bytes32 h = keccak256(abi.encodePacked(i));
            uint256 gasBefore = gasleft();
            hs.addWithdrawal(h);
            uint256 gasUsed = gasBefore - gasleft();

            // Assert gas per call stays below block gas limit
            assertLt(gasUsed, 30_000_000,
                string(abi.encodePacked("OOG at i=", vm.toString(i))));
        }

        vm.stopPrank();
    }
}
```

Running this test locally on unmodified code will show gas-per-call growing linearly with `i` and the assertion failing around `i ≈ 14 000–15 000`, confirming the block-gas-limit breach.

### Citations

**File:** contracts/utils/HashStorage.sol (L22-22)
```text
    mapping(bytes32 txHash => bool isClaimed) public claimedWithdrawals;
```

**File:** contracts/utils/HashStorage.sol (L76-78)
```text
        if (claimedWithdrawals[txHash]) {
            revert HashAlreadyClaimed();
        }
```

**File:** contracts/utils/HashStorage.sol (L80-86)
```text
        uint256 length = withdrawalTxHashes.length;

        for (uint256 i = 0; i < length; i++) {
            if (withdrawalTxHashes[i] == txHash) {
                revert DuplicateHash();
            }
        }
```

**File:** contracts/utils/HashStorage.sol (L88-89)
```text
        claimedWithdrawals[txHash] = false;
        withdrawalTxHashes.push(txHash);
```

**File:** contracts/utils/HashStorage.sol (L117-118)
```text
                withdrawalTxHashes[i] = withdrawalTxHashes[withdrawalTxHashes.length - 1];
                withdrawalTxHashes.pop();
```
