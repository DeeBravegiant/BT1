### Title
Manager Can Increase `withdrawalDelayBlocks` During Active Withdrawal Requests, Temporarily Freezing User Funds - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager.setWithdrawalDelayBlocks` allows the LRT manager to update the global `withdrawalDelayBlocks` parameter at any time with no guard against active pending withdrawal requests. Because both `_processWithdrawalCompletion` and `_unlockWithdrawalRequests` evaluate the delay check against the **current** `withdrawalDelayBlocks` value rather than the value that was in effect when the request was created, increasing this parameter mid-flight retroactively re-locks withdrawal requests that were already past their original deadline, temporarily freezing user funds.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is a two-phase process:

1. **`initiateWithdrawal`** — user burns rsETH and a `WithdrawalRequest` is stored with `withdrawalStartBlock = block.number`.
2. **`completeWithdrawal` / `unlockQueue`** — the delay check is evaluated as:

```solidity
// _processWithdrawalCompletion, line 715
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks)
    revert WithdrawalDelayNotPassed();
```

```solidity
// _unlockWithdrawalRequests, line 795
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

Both reads use the **live** storage value of `withdrawalDelayBlocks`, not a snapshot captured at request time.

The setter has no active-request guard:

```solidity
// setWithdrawalDelayBlocks, lines 338-344
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
}
```

The initial value is `8 days / 12 seconds = 57,600 blocks` (set in `initialize`, line 94). The ceiling is `16 days / 12 seconds = 115,200 blocks`. The manager can therefore double the effective delay at any moment without any check for in-flight requests.

---

### Impact Explanation

Any user who initiated a withdrawal and whose request has already passed the original 8-day deadline will be unable to call `completeWithdrawal` after the manager raises the delay to 16 days. Their rsETH has already been transferred to the contract (line 166) and is held there until the new, longer deadline passes. This constitutes a **temporary freezing of funds** for all affected withdrawers. The freeze can last up to an additional 8 days (the maximum increase permitted).

**Impact: Medium — Temporary freezing of funds.**

---

### Likelihood Explanation

The LRT manager role is a live operational key used for routine protocol maintenance. No key compromise is required; the manager simply calls `setWithdrawalDelayBlocks` within their granted permissions. The scenario is realistic whenever the manager adjusts the delay for operational reasons (e.g., responding to an EigenLayer upgrade that extends its own withdrawal period) while users already have pending requests past the old deadline.

---

### Recommendation

Snapshot `withdrawalDelayBlocks` into each `WithdrawalRequest` at the time of `initiateWithdrawal` and use that stored value in both `_processWithdrawalCompletion` and `_unlockWithdrawalRequests`. This ensures that a parameter change never retroactively affects requests that were already submitted under a different delay. Alternatively, add a check in `setWithdrawalDelayBlocks` that reverts if any withdrawal requests are currently pending (i.e., `nextUnusedNonce[asset] > nextLockedNonce[asset]` for any supported asset), mirroring the mitigation recommended in the referenced report.

---

### Proof of Concept

1. `withdrawalDelayBlocks` is initialized to `57,600` (8 days at 12 s/block).
2. Alice calls `initiateWithdrawal(ETH, rsETHAmount, "")` at block **N**. Her rsETH is transferred to the contract; `withdrawalStartBlock = N` is stored.
3. At block **N + 57,600** Alice is past the delay and eligible to complete.
4. Before Alice's transaction lands, the LRT manager calls `setWithdrawalDelayBlocks(115_200)` (16 days). No revert — no active-request check exists.
5. Alice calls `completeWithdrawal(ETH, "")` at block **N + 57,600**.
6. `_processWithdrawalCompletion` evaluates: `N + 57,600 < N + 115,200` → **true** → `revert WithdrawalDelayNotPassed`.
7. Alice's funds remain locked in the contract for an additional ~8 days, despite her having waited the full original delay.

The same block-level check at line 795 inside `_unlockWithdrawalRequests` means the operator-triggered `unlockQueue` path is equally blocked for Alice's request. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L90-94)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L338-344)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L714-715)
```text
        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L750-753)
```text
        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L793-795)
```text

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```
