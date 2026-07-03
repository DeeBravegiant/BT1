### Title
Smart-contract ETH withdrawal recipients without a payable fallback permanently freeze rsETH in `LRTWithdrawalManager` — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

When a smart-contract account initiates a queued ETH withdrawal via `initiateWithdrawal`, its rsETH is immediately escrowed in `LRTWithdrawalManager`. If that contract cannot receive native ETH (no `payable` fallback, or one that reverts), every subsequent call to `completeWithdrawal` will revert, and because no cancellation path exists, the escrowed rsETH is permanently frozen.

---

### Finding Description

**Step 1 — rsETH is escrowed on initiation.**

`initiateWithdrawal` pulls rsETH from the caller into the withdrawal manager before any ETH is involved: [1](#0-0) 

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

The withdrawal request is then stored and the asset amount is committed. There is no way to undo this step.

**Step 2 — Completion sends ETH to the original caller.**

`_processWithdrawalCompletion` (called by both `completeWithdrawal` and `completeWithdrawalForUser`) ends with: [2](#0-1) 

```solidity
_transferAsset(asset, user, request.expectedAssetAmount);
```

For `asset == ETH_TOKEN`, `_transferAsset` uses a low-level `call{ value: amount }("")`. If the recipient contract has no `payable` fallback (or one that reverts), the call returns `false`, the helper reverts with `TransferFailed`, and the entire transaction is rolled back — including the nonce pop and the `unlockedWithdrawalsCount` decrement.

**Step 3 — No cancellation path exists.**

There is no function in `LRTWithdrawalManager` that allows a user to cancel a pending withdrawal request and reclaim their rsETH. The only administrative sweep function, `sweepRemainingAssets`, is explicitly blocked while any unlocked withdrawal exists: [3](#0-2) 

```solidity
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

Because the stuck withdrawal keeps `unlockedWithdrawalsCount[asset] > 0`, even this administrative escape hatch is unavailable. The rsETH is irrecoverable without a contract upgrade.

**Step 4 — The operator path also fails.**

`completeWithdrawalForUser` is the only alternative completion route, but it calls the same `_processWithdrawalCompletion` and will revert identically. The inline comment even acknowledges the issue while incorrectly dismissing it: [4](#0-3) 

```
/// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
```

The comment is wrong: the scenario is not a gas-grief but a permanent revert, and the impact is a permanent fund freeze.

---

### Impact Explanation

A smart-contract user (multisig, DAO treasury, DeFi vault, or any contract without a `payable` fallback) that calls `initiateWithdrawal` for ETH will have its rsETH permanently locked in `LRTWithdrawalManager` with no recovery path short of a proxy upgrade. This satisfies **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

Smart contracts routinely hold liquid restaking tokens and interact with withdrawal queues programmatically (e.g., multisig-controlled treasuries, yield aggregators, automated rebalancers). Many such contracts deliberately omit a `payable` fallback to prevent accidental ETH acceptance. The entry path (`initiateWithdrawal`) is fully permissionless and requires no special role. Likelihood is **Medium**.

---

### Recommendation

1. **Add a withdrawal cancellation function** that allows the original requester to cancel a pending (still-locked) withdrawal request and receive their rsETH back.
2. **Allow the user to specify a separate ETH recipient address** at completion time, so a smart contract can redirect ETH to an EOA or a contract that can receive it.
3. **Document the requirement** that callers initiating ETH withdrawals must be able to receive native ETH, analogous to the documentation fix recommended in the original Aave report.
4. **Consider a pull-payment pattern** for ETH: instead of pushing ETH to the user in `completeWithdrawal`, credit an internal balance that the user can `claim()` separately, eliminating the revert-on-receive risk entirely.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface IWithdrawalManager {
    function initiateWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId) external;
    function completeWithdrawal(address asset, string calldata referralId) external;
}

interface IERC20 {
    function approve(address, uint256) external returns (bool);
}

/// @notice No payable fallback — cannot receive ETH
contract NoPayableFallbackVault {
    address constant ETH_TOKEN = 0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE;

    function approveAndInitiate(
        address rsETH,
        address withdrawalManager,
        uint256 rsETHAmount
    ) external {
        IERC20(rsETH).approve(withdrawalManager, rsETHAmount);
        // rsETH is now escrowed in LRTWithdrawalManager — cannot be recovered
        IWithdrawalManager(withdrawalManager).initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
    }

    function tryComplete(address withdrawalManager) external {
        // Always reverts with TransferFailed because this contract has no payable fallback.
        // rsETH remains permanently frozen in LRTWithdrawalManager.
        IWithdrawalManager(withdrawalManager).completeWithdrawal(ETH_TOKEN, "");
    }
    // No receive() or fallback() payable
}
```

**Attack flow:**
1. `NoPayableFallbackVault` holds rsETH and calls `approveAndInitiate` → rsETH is escrowed.
2. Operator calls `unlockQueue` → the withdrawal is unlocked, `unlockedWithdrawalsCount[ETH_TOKEN]++`.
3. `tryComplete` is called → `_transferAsset` attempts `call{ value: amount }("")` to `NoPayableFallbackVault` → reverts → transaction rolls back.
4. Step 3 can be repeated indefinitely; it always reverts.
5. `sweepRemainingAssets` is blocked because `unlockedWithdrawalsCount[ETH_TOKEN] > 0`.
6. rsETH is permanently frozen with no on-chain recovery path.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
```

**File:** contracts/LRTWithdrawalManager.sol (L403-403)
```text
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```
