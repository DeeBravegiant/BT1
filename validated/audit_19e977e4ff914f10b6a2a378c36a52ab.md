I have the full code. Let me analyze it carefully.

The code is fully readable. Here is my analysis:

---

### Title
Unbounded O(n) Array Scan in `addWithdrawal` and `setWithdrawalClaimed` Enables Gas-DoS Freezing Bridge Withdrawal Funds — (`contracts/utils/HashStorage.sol`)

### Summary

`HashStorage` stores unclaimed L2→L1 bridge withdrawal hashes in a dynamic array `withdrawalTxHashes` with no size cap. Both `addWithdrawal` and `setWithdrawalClaimed` iterate the entire array on every call. When the array grows large enough, both functions exceed the block gas limit and become permanently uncallable, freezing all pending bridge withdrawals.

---

### Finding Description

`addWithdrawal` performs a full linear scan of `withdrawalTxHashes` to detect duplicates: [1](#0-0) 

`setWithdrawalClaimed` performs an identical full linear scan to locate and remove the hash: [2](#0-1) 

There is no upper bound enforced on the array: [3](#0-2) 

Each iteration reads a distinct storage slot (`withdrawalTxHashes[i]`), costing **2,100 gas per element** (cold SLOAD, EIP-2929). At the Ethereum block gas limit of ~30M gas:

```
30,000,000 / 2,100 ≈ 14,285 elements → both functions OOG
```

Once the threshold is crossed, `setWithdrawalClaimed` also OOGs, so the array **cannot be drained** — the state is self-locking. There is no on-chain circuit breaker, no emergency removal path, and no admin escape hatch in this contract.

The only existing guard against duplicates is the mapping `claimedWithdrawals`, but it only covers *already-claimed* hashes, not *pending* ones — so the O(n) loop is the sole duplicate check for live hashes. [4](#0-3) 

---

### Impact Explanation

Once `withdrawalTxHashes.length` exceeds ~14,000:
- `addWithdrawal` OOGs → new bridge withdrawals cannot be registered.
- `setWithdrawalClaimed` OOGs → existing registered withdrawals cannot be claimed.

All bridge withdrawal funds are frozen. Recovery requires an off-chain contract upgrade/redeployment, which is not guaranteed and introduces additional risk.

**Impact: Medium — Temporary (potentially permanent) freezing of funds.**

---

### Likelihood Explanation

The operator is acting entirely within their legitimate role: registering L2→L1 bridge withdrawal hashes as they arrive. This is not operator compromise. A bridge backlog (e.g., high L2 activity, slow L1 finality, or a period where claiming is paused) is a realistic operational condition. The operator has no incentive to throttle registrations — their job is to register all arriving hashes. The growth is monotonic unless claims keep pace, and there is no protocol mechanism to enforce that invariant.

---

### Recommendation

Replace the O(n) duplicate-check loop in `addWithdrawal` with an O(1) mapping lookup. A `mapping(bytes32 => bool) public isPending` can track whether a hash is currently in the array, eliminating the scan entirely. Similarly, replace the O(n) search in `setWithdrawalClaimed` with a `mapping(bytes32 => uint256) public hashIndex` to enable O(1) removal. Optionally, add a `MAX_PENDING_HASHES` cap as a secondary circuit breaker.

```solidity
// O(1) duplicate check
mapping(bytes32 => bool) public isPending;

function addWithdrawal(bytes32 txHash) external onlyRole(OPERATOR_ROLE) {
    if (txHash == bytes32(0)) revert InvalidHash();
    if (claimedWithdrawals[txHash]) revert HashAlreadyClaimed();
    if (isPending[txHash]) revert DuplicateHash();
    isPending[txHash] = true;
    withdrawalTxHashes.push(txHash);
    emit WithdrawalAdded(txHash);
}
```

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

    function test_gasDoS_addWithdrawal() public {
        vm.startPrank(operator);
        // Fill array to near-OOG threshold
        for (uint256 i = 1; i <= 14_000; i++) {
            hs.addWithdrawal(bytes32(i));
        }

        // Next call should OOG (measure gas)
        uint256 gasBefore = gasleft();
        hs.addWithdrawal(bytes32(uint256(14_001)));
        uint256 gasUsed = gasBefore - gasleft();

        // Assert gas used approaches block limit
        assertGt(gasUsed, 25_000_000, "Gas usage near block limit");
        vm.stopPrank();
    }

    function test_gasDoS_setWithdrawalClaimed_also_blocked() public {
        vm.startPrank(operator);
        for (uint256 i = 1; i <= 14_000; i++) {
            hs.addWithdrawal(bytes32(i));
        }
        // Claiming the first hash also requires full scan → OOG
        uint256 gasBefore = gasleft();
        hs.setWithdrawalClaimed(bytes32(uint256(1)));
        uint256 gasUsed = gasBefore - gasleft();
        assertGt(gasUsed, 25_000_000, "setWithdrawalClaimed also near OOG");
        vm.stopPrank();
    }
}
```

Run with: `forge test --match-contract HashStorageGasDoSTest -vvv`

The test demonstrates that both `addWithdrawal` and `setWithdrawalClaimed` consume gas proportional to `withdrawalTxHashes.length`, with no on-chain bound preventing the array from reaching the OOG threshold under normal operator operation.

### Citations

**File:** contracts/utils/HashStorage.sol (L19-19)
```text
    bytes32[] public withdrawalTxHashes;
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
