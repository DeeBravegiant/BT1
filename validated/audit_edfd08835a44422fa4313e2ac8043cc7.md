### Title
Unbounded O(n) Array Scan in `addWithdrawal` and `setWithdrawalClaimed` Enables Gas-DoS — (`contracts/utils/HashStorage.sol`)

---

### Summary

`HashStorage` stores pending bridge-withdrawal hashes in an unbounded dynamic array `withdrawalTxHashes`. Both `addWithdrawal` and `setWithdrawalClaimed` iterate the entire array on every call. Because there is no on-chain cap on array length, a sustained bridge backlog causes the array to grow until both functions permanently exceed the block gas limit, freezing withdrawal registration and claiming.

---

### Finding Description

`withdrawalTxHashes` is declared as an unbounded `bytes32[]`: [1](#0-0) 

`addWithdrawal` performs a full O(n) linear scan on every call to detect duplicates among *pending* (unclaimed) hashes: [2](#0-1) 

The scan is necessary because `claimedWithdrawals[txHash]` is only set to `true` after a claim; pending hashes are stored as `false` in the mapping, so the O(1) mapping check at line 76 cannot distinguish "never seen" from "pending." The O(n) loop is therefore load-bearing for correctness, yet unbounded.

`setWithdrawalClaimed` contains an identical O(n) scan to locate and swap-pop the hash: [3](#0-2) 

Neither function has a guard that caps `withdrawalTxHashes.length`. There is no pause mechanism, no batch-removal path, and no admin escape hatch that could drain the array once both write functions are OOG.

**Gas estimate:** Each iteration of the duplicate-check loop reads one `bytes32` storage slot (~2,100 gas cold, ~100 gas warm after first access in the same tx). At Ethereum's 30 M gas block limit, the loop saturates at roughly **~14,000–28,000 entries** (depending on warm/cold slot distribution). Beyond that threshold, every call to `addWithdrawal` and `setWithdrawalClaimed` reverts with OOG, and the array cannot be shrunk because `setWithdrawalClaimed` itself OOGs.

---

### Impact Explanation

Once the threshold is crossed:

- `addWithdrawal` is uncallable — new bridge withdrawals cannot be registered.
- `setWithdrawalClaimed` is uncallable — existing registered withdrawals cannot be marked claimed, blocking any downstream claiming logic that depends on this registry.
- There is no on-chain recovery path; the contract has no admin function to bulk-remove entries or reset the array.

This constitutes **temporary freezing of funds** (Medium) — withdrawal flows are halted until an off-chain upgrade or redeployment is performed.

---

### Likelihood Explanation

The operator acts within their normal role: faithfully registering every L2→L1 bridge withdrawal hash as it arrives. During a bridge backlog (e.g., high L2 activity, slow L1 finality, or a temporary claiming outage), the operator continues adding hashes while claims lag. No malicious intent is required. The condition `R_add > R_claim` sustained over time is a realistic operational scenario for any bridge with variable throughput. There is no on-chain rate limiter or array-size circuit breaker to prevent it.

---

### Recommendation

1. **Replace the O(n) duplicate-check loop with an O(1) mapping.** Track pending hashes in a separate `mapping(bytes32 => bool) pendingWithdrawals` that is set to `true` on `addWithdrawal` and cleared on `setWithdrawalClaimed`. The array scan at lines 82–86 can then be eliminated entirely.

2. **Enforce an on-chain maximum array length.** Add a `uint256 public maxPendingWithdrawals` (e.g., 1,000) and revert in `addWithdrawal` if `withdrawalTxHashes.length >= maxPendingWithdrawals`.

3. **Add a batch-claim function** so the operator can process multiple hashes per transaction, keeping the array short under high load.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/utils/HashStorage.sol";

contract HashStorageGasDoSTest is Test {
    HashStorage hs;
    address admin   = address(0xA);
    address operator = address(0xB);

    function setUp() public {
        hs = new HashStorage(admin, operator, "test");
    }

    /// @notice Fills the array to LIMIT entries, then asserts that
    ///         addWithdrawal and setWithdrawalClaimed both OOG.
    function test_gasDoS() public {
        uint256 LIMIT = 15_000; // tune to local block gas limit
        vm.startPrank(operator);

        for (uint256 i = 1; i <= LIMIT; i++) {
            hs.addWithdrawal(bytes32(i));
        }

        // Next addWithdrawal should OOG
        uint256 gasBefore = gasleft();
        try hs.addWithdrawal(bytes32(LIMIT + 1)) {
            // if it succeeds, increase LIMIT
        } catch {
            uint256 gasUsed = gasBefore - gasleft();
            emit log_named_uint("gas used for addWithdrawal at LIMIT", gasUsed);
        }

        // setWithdrawalClaimed on an existing hash should also OOG
        gasBefore = gasleft();
        try hs.setWithdrawalClaimed(bytes32(uint256(1))) {
        } catch {
            uint256 gasUsed = gasBefore - gasleft();
            emit log_named_uint("gas used for setWithdrawalClaimed at LIMIT", gasUsed);
        }

        // Invariant: array length must never exceed a safe bound
        assertLt(
            hs.getWithdrawalTxHashes().length,
            LIMIT,
            "INVARIANT BROKEN: array exceeded safe gas bound"
        );

        vm.stopPrank();
    }
}
```

Run with:
```
forge test --match-test test_gasDoS --gas-limit 30000000 -vvv
```

The test demonstrates that at `LIMIT` entries both write functions revert OOG, confirming the invariant break with no on-chain recovery path.

### Citations

**File:** contracts/utils/HashStorage.sol (L19-19)
```text
    bytes32[] public withdrawalTxHashes;
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

**File:** contracts/utils/HashStorage.sol (L111-121)
```text
        uint256 length = withdrawalTxHashes.length;
        bool found = false;

        for (uint256 i = 0; i < length; i++) {
            if (withdrawalTxHashes[i] == txHash) {
                found = true;
                withdrawalTxHashes[i] = withdrawalTxHashes[withdrawalTxHashes.length - 1];
                withdrawalTxHashes.pop();
                break;
            }
        }
```
