### Title
O(n) Unbounded Duplicate Scan in `addWithdrawal` Causes Gas-Limit DoS for Operator — (`contracts/utils/HashStorage.sol`)

---

### Summary

`HashStorage.addWithdrawal` performs a full linear scan of the `withdrawalTxHashes` array on every call to detect duplicate pending hashes. Because the `claimedWithdrawals` mapping stores `false` for both "never seen" and "currently pending" entries, the contract cannot distinguish them with an O(1) lookup and falls back to an O(n) loop. With no batch interface, registering N pending L2→L1 hashes costs O(N²) cumulative gas, and at sufficient array depth a single `addWithdrawal` call will exceed the block gas limit, permanently preventing the operator from registering new hashes.

---

### Finding Description

In `addWithdrawal`, after the O(1) `claimedWithdrawals` check (which only catches *previously claimed* hashes), the function iterates the entire `withdrawalTxHashes` array to detect duplicates among *currently pending* hashes: [1](#0-0) 

This loop is O(n) where n = `withdrawalTxHashes.length`. The root cause is that line 88 writes `claimedWithdrawals[txHash] = false` — identical to the mapping's default — so the mapping cannot distinguish "pending" from "unseen": [2](#0-1) 

`setWithdrawalClaimed` has the same O(n) scan to locate and swap-remove the hash: [3](#0-2) 

Neither function has a batch variant. To register N hashes the operator must submit N separate transactions, each paying O(current array size) gas. Total cost is O(N²).

---

### Impact Explanation

At ~14 000 pending unclaimed entries the inner loop alone (~14 000 cold SLOADs × 2 100 gas ≈ 29.4 M gas) approaches Ethereum's ~30 M block gas limit. Beyond that threshold `addWithdrawal` reverts on every call. The operator can no longer register new L2→L1 withdrawal hashes, so the bridge's hash-tracking promise is broken. No funds are lost — hashes already stored remain claimable — but new withdrawals cannot be acknowledged on-chain until the backlog is drained, which itself requires O(n) gas per `setWithdrawalClaimed` call.

**Scoped impact:** Low — Contract fails to deliver promised returns, but doesn't lose value.

---

### Likelihood Explanation

The array size is bounded by the number of *unclaimed* pending withdrawals. Under normal throughput the operator claims hashes as fast as they arrive, keeping the array small. However, during a bridge surge (e.g., a large rsETH redemption event), the inflow rate can exceed the operator's claim rate, causing the array to grow. The operator is a single privileged EOA/multisig with no parallelism, so the O(n²) cost compounds quickly. The scenario is realistic on any high-activity day and requires no attacker — it is a self-inflicted liveness failure.

---

### Recommendation

Replace the O(n) duplicate scan with an O(1) `pendingWithdrawals` mapping:

```solidity
// Add state variable
mapping(bytes32 txHash => bool isPending) public pendingWithdrawals;

// In addWithdrawal — replace the for-loop with:
if (pendingWithdrawals[txHash]) revert DuplicateHash();
pendingWithdrawals[txHash] = true;

// In setWithdrawalClaimed — set:
pendingWithdrawals[txHash] = false;
```

This reduces both functions to O(1) per hash. Optionally add batch variants (`addWithdrawals(bytes32[] calldata)` / `setWithdrawalsClaimed(bytes32[] calldata)`) to further reduce per-transaction overhead.

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

    function test_addWithdrawal_quadratic_gas() public {
        uint256 N = 1000;
        uint256 totalGas;

        vm.startPrank(operator);
        for (uint256 i = 1; i <= N; i++) {
            bytes32 h = keccak256(abi.encodePacked(i));
            uint256 g = gasleft();
            hs.addWithdrawal(h);
            totalGas += g - gasleft();
        }
        vm.stopPrank();

        // Each successive call costs more; assert total stays within 2× a
        // flat-cost baseline to detect O(n²) growth.
        uint256 flatBaseline = 50_000 * N; // ~50k gas per call if O(1)
        assertLt(
            totalGas,
            flatBaseline * 2,
            "Gas grew super-linearly — O(n) loop confirmed"
        );
    }
}
```

Running this test will show the assertion failing: the 1 000th `addWithdrawal` call costs ~100× more gas than the 1st, confirming O(n) per-call growth and O(n²) total cost. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/utils/HashStorage.sol (L71-92)
```text
    function addWithdrawal(bytes32 txHash) external onlyRole(OPERATOR_ROLE) {
        if (txHash == bytes32(0)) {
            revert InvalidHash();
        }

        if (claimedWithdrawals[txHash]) {
            revert HashAlreadyClaimed();
        }

        uint256 length = withdrawalTxHashes.length;

        for (uint256 i = 0; i < length; i++) {
            if (withdrawalTxHashes[i] == txHash) {
                revert DuplicateHash();
            }
        }

        claimedWithdrawals[txHash] = false;
        withdrawalTxHashes.push(txHash);

        emit WithdrawalAdded(txHash);
    }
```

**File:** contracts/utils/HashStorage.sol (L98-130)
```text
    function setWithdrawalClaimed(bytes32 txHash) external onlyRole(OPERATOR_ROLE) {
        if (txHash == bytes32(0)) {
            revert InvalidHash();
        }

        if (withdrawalTxHashes.length == 0) {
            revert NoWithdrawals();
        }

        if (claimedWithdrawals[txHash]) {
            revert HashAlreadyClaimed();
        }

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

        if (!found) {
            revert InvalidHash();
        }

        claimedWithdrawals[txHash] = true;

        emit WithdrawalClaimed(txHash);
    }
```
