Audit Report

## Title
Unbounded Withdrawal Queue Accumulation Enables Temporary Freeze of User Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.initiateWithdrawal()` imposes no per-user or global count limit on queued withdrawal requests. An attacker holding sufficient rsETH can flood the global FIFO queue with many minimum-sized requests, committing all available asset capacity and blocking legitimate users from initiating withdrawals for at least `withdrawalDelayBlocks` (8 days). The same flood forces the operator's `unlockQueue` to iterate through a large number of entries before reaching legitimate users' requests.

## Finding Description
`initiateWithdrawal()` accepts any rsETH amount at or above `minRsEthAmountToWithdraw[asset]` and unconditionally appends a new entry to the global queue via `_addUserWithdrawalRequest()`:

```solidity
// LRTWithdrawalManager.sol:162-175
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
...
assetsCommitted[asset] += expectedAssetAmount;
_addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

Inside `_addUserWithdrawalRequest`, the nonce is pushed to the user's deque and `nextUnusedNonce` is incremented with no count check:

```solidity
// LRTWithdrawalManager.sol:756-757
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

The only guard against over-commitment is the asset-amount check:

```solidity
// LRTWithdrawalManager.sol:170
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

This prevents committing more assets than exist, but does **not** limit the number of individual requests. An attacker can split their rsETH into `totalAvailableAssets / minRsEthAmountToWithdraw` separate requests, each consuming the minimum amount, filling the entire available asset capacity with attacker-controlled queue entries. Once `assetsCommitted[asset]` equals total available assets, every subsequent `initiateWithdrawal` call from legitimate users reverts with `ExceedAmountToWithdraw`.

The operator's `_unlockWithdrawalRequests` processes entries sequentially in FIFO order:

```solidity
// LRTWithdrawalManager.sol:790-814
while (nextLockedNonce_ < firstExcludedIndex) {
    bytes32 requestId = getRequestId(asset, nextLockedNonce_);
    WithdrawalRequest storage request = withdrawalRequests[requestId];
    if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);
    if (availableAssetAmount < payoutAmount) break;
    ...
    unchecked { nextLockedNonce_++; }
}
```

The loop must advance through every attacker request before reaching legitimate users' requests. While the operator can control `firstExcludedIndex` to batch calls, the total work required is proportional to the number of attacker entries, requiring many batched `unlockQueue` calls to drain the queue.

By contrast, `KernelDepositPool` — another contract in the same repository — explicitly enforces `MAX_WITHDRAWALS_PER_USER = 100` and checks it on every `initiateWithdrawal` call:

```solidity
// KernelDepositPool.sol:38,323
uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

`LRTWithdrawalManager` has no equivalent guard.

## Impact Explanation
**Temporary freezing of funds (Medium)**: While the attacker's requests occupy all committed asset capacity, `getAvailableAssetAmount(asset)` returns 0, causing every subsequent `initiateWithdrawal` call from legitimate users to revert with `ExceedAmountToWithdraw`. Legitimate users cannot queue withdrawals until the attacker's requests are processed and `assetsCommitted` decreases. The freeze lasts at least `withdrawalDelayBlocks` (default 8 days). This matches the allowed Medium impact of "Temporary freezing of funds."

**Unbounded gas consumption (Medium)**: `_unlockWithdrawalRequests` iterates linearly over all queued entries. With a large number of attacker-created entries, the operator must process them in many batched `unlockQueue` calls before legitimate users' requests can be reached, as the queue is strictly FIFO. This matches the allowed Medium impact of "Unbounded gas consumption."

## Likelihood Explanation
The attacker must hold rsETH proportional to the total available assets and lock it for the withdrawal delay period (8 days). This is a real economic cost. However, the attack requires no special privileges — `initiateWithdrawal` is callable by any rsETH holder — and the mechanism is straightforward. A well-capitalised attacker (e.g., a competing protocol or a large rsETH holder) can execute this at any time the contract is unpaused. The attack is repeatable: after the attacker's requests are drained, they can re-acquire rsETH and repeat.

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

2. Optionally, add a global cap on `nextUnusedNonce - nextLockedNonce` (total pending requests per asset) to bound the queue depth independently of per-user limits.

## Proof of Concept
1. Protocol has 1000 ETH in available assets; `minRsEthAmountToWithdraw[ETH]` is set to `1e15` (0.001 ETH worth of rsETH).
2. Attacker acquires rsETH equivalent to 1000 ETH and calls `initiateWithdrawal(ETH, 1e15, "")` in a loop ~1,000,000 times (splitting across blocks as needed), each time committing 0.001 ETH of asset capacity.
3. After the attacker's requests fill `assetsCommitted[ETH] == totalAssets`, every subsequent call by a legitimate user reverts: `ExceedAmountToWithdraw`.
4. The operator calls `unlockQueue(ETH, nextUnusedNonce, ...)`. The `_unlockWithdrawalRequests` loop must iterate through all ~1,000,000 attacker entries before reaching any legitimate request, requiring many batched `unlockQueue` calls.
5. Legitimate users' withdrawals are frozen for at least 8 days (the withdrawal delay) until the attacker's requests are drained through many batched `unlockQueue` calls.

**Foundry test plan**:
```solidity
function testWithdrawalQueueFlood() public {
    // Setup: fund protocol with 1000 ETH of available assets
    // Attacker: mint rsETH equivalent to 1000 ETH
    // Loop: call initiateWithdrawal(ETH, minRsEthAmountToWithdraw[ETH]) until assetsCommitted == totalAssets
    // Assert: legitimate user's initiateWithdrawal reverts with ExceedAmountToWithdraw
    // Assert: operator must call unlockQueue many times to drain attacker entries
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-175)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L755-757)
```text
        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

**File:** contracts/LRTWithdrawalManager.sol (L790-815)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L37-38)
```text
    /// @notice The maximum number of open (unclaimed) withdrawals allowed per user at any time
    uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-323)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```
