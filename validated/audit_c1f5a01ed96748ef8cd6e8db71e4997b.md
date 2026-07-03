Audit Report

## Title
Unbounded Withdrawal Queue Accumulation Enables Temporary Freeze of User Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.initiateWithdrawal()` imposes no per-user or global count limit on queued withdrawal requests. An attacker holding sufficient rsETH can flood the global FIFO queue with minimum-sized requests, exhausting all available asset capacity and blocking legitimate users from initiating withdrawals for at least the withdrawal delay period. The same flood forces the operator's `unlockQueue` to iterate through a large number of entries, increasing operational overhead significantly.

## Finding Description
`initiateWithdrawal()` enforces only two guards before appending to the queue:

```solidity
// LRTWithdrawalManager.sol:162-170
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
...
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

The amount check prevents committing more assets than exist, but does not limit the number of individual requests. `_addUserWithdrawalRequest` unconditionally pushes to the user's deque and increments the global nonce:

```solidity
// LRTWithdrawalManager.sol:756-757
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

An attacker holding rsETH equivalent to the total available assets can call `initiateWithdrawal` with `minRsEthAmountToWithdraw` per call, creating `totalAvailableAssets / minAmount` separate entries. Once `assetsCommitted[asset]` equals total available assets, every subsequent legitimate call reverts with `ExceedAmountToWithdraw`.

`_unlockWithdrawalRequests` processes entries sequentially:

```solidity
// LRTWithdrawalManager.sol:790-814
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    if (availableAssetAmount < payoutAmount) break;
    assetsCommitted[asset] -= request.expectedAssetAmount;
    ...
    unchecked { nextLockedNonce_++; }
}
```

The operator must advance through every attacker entry before `assetsCommitted` decreases enough to allow legitimate users to queue. While the operator can bound each `unlockQueue` call via `firstExcludedIndex`, draining a large attacker queue requires many sequential operator transactions, extending the freeze duration.

By contrast, `KernelDepositPool` in the same repository explicitly enforces a cap:

```solidity
// KernelDepositPool.sol:38
uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
// KernelDepositPool.sol:323
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

`LRTWithdrawalManager` has no equivalent guard.

## Impact Explanation
**Temporary freezing of funds (Medium):** While attacker requests occupy all committed asset capacity, `getAvailableAssetAmount(asset)` returns 0, causing every subsequent `initiateWithdrawal` from legitimate users to revert. The freeze persists for at least `withdrawalDelayBlocks` (default ~8 days) until the operator drains the attacker's entries through batched `unlockQueue` calls and `assetsCommitted` decreases. This matches the allowed Medium impact: temporary freezing of funds.

**Unbounded gas consumption (Medium):** Although the operator can bound each `unlockQueue` call via `firstExcludedIndex`, a very large attacker queue forces many sequential operator transactions to drain it, which matches the allowed Medium impact of unbounded gas consumption in aggregate.

## Likelihood Explanation
The attacker requires rsETH proportional to total available assets and must lock it for the withdrawal delay (~8 days). No special privileges are needed — `initiateWithdrawal` is a public function callable by any rsETH holder. A well-capitalized attacker (large rsETH holder or competing protocol) can execute this at any time the contract is unpaused. The attack is repeatable: after the attacker's requests are drained, they can re-acquire rsETH and repeat.

## Recommendation
1. Introduce a per-user pending withdrawal request cap in `initiateWithdrawal`, analogous to `KernelDepositPool.MAX_WITHDRAWALS_PER_USER`:

```solidity
uint256 public maxPendingWithdrawalsPerUser; // e.g. 100

function initiateWithdrawal(...) external {
    ...
    if (userAssociatedNonces[asset][msg.sender].length() >= maxPendingWithdrawalsPerUser)
        revert TooManyPendingWithdrawals();
    ...
}
```

2. Optionally, add a global cap on `nextUnusedNonce[asset] - nextLockedNonce[asset]` to bound total pending queue depth per asset independently of per-user limits.

## Proof of Concept
1. Protocol has 1000 ETH in available assets; `minRsEthAmountToWithdraw[ETH]` = `1e15` (0.001 ETH worth of rsETH).
2. Attacker acquires rsETH equivalent to 1000 ETH and calls `initiateWithdrawal(ETH, 1e15, "")` ~1,000,000 times across blocks, each committing 0.001 ETH of asset capacity.
3. After attacker's requests fill `assetsCommitted[ETH]` to total available assets, every subsequent legitimate user call reverts: `ExceedAmountToWithdraw`.
4. Operator calls `unlockQueue(ETH, ...)` with bounded `firstExcludedIndex` batches. Each batch processes a subset of attacker entries, requiring many sequential transactions to drain the queue.
5. Legitimate users' withdrawals are frozen for at least 8 days until the attacker's entries are fully drained.

**Foundry test sketch:**
```solidity
function testQueueFlood() public {
    // Attacker splits rsETH into N minimum-sized requests
    uint256 N = availableAssets / minRsEthAmountToWithdraw;
    for (uint256 i = 0; i < N; i++) {
        vm.prank(attacker);
        withdrawalManager.initiateWithdrawal(asset, minRsEthAmountToWithdraw, "");
    }
    // Legitimate user attempt reverts
    vm.prank(legitimateUser);
    vm.expectRevert(ILRTWithdrawalManager.ExceedAmountToWithdraw.selector);
    withdrawalManager.initiateWithdrawal(asset, largeAmount, "");
}
```