Audit Report

## Title
Unbounded Withdrawal Queue Enables Temporary Freezing of Funds via Dust Withdrawal Requests — (`contracts/LRTWithdrawalManager.sol`)

## Summary
`minRsEthAmountToWithdraw[asset]` is never initialised in `initialize()`, defaulting to zero. This collapses the minimum-amount guard in `initiateWithdrawal` to a zero-check only, allowing any rsETH holder to enqueue arbitrarily many dust requests (e.g., 1 wei each). Because the withdrawal queue is strictly FIFO and `_unlockWithdrawalRequests` iterates from `nextLockedNonce` to a caller-supplied `firstExcludedIndex`, legitimate users whose requests are queued behind thousands of dust entries cannot complete their withdrawals until every preceding dust entry is processed, temporarily freezing their rsETH in the contract.

## Finding Description
**Root cause — uninitialized minimum:**
`initialize()` (lines 90–98) sets only `withdrawalDelayBlocks` and `lrtConfig`; `minRsEthAmountToWithdraw` is never written. Its default value is `0`. The guard in `initiateWithdrawal` (line 162) is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

When the mapping value is `0`, the second condition (`rsETHUnstaked < 0`) is always false for `uint256`, so the check reduces to `rsETHUnstaked == 0`. Any positive amount — including 1 wei — passes.

**Queue flooding:**
An attacker holding rsETH calls `initiateWithdrawal(asset, 1, "")` N times. Each call:
- Transfers 1 wei rsETH to the contract (line 166).
- Computes `expectedAssetAmount ≈ 1 wei` via `getExpectedAssetAmount` (line 168).
- Increments `assetsCommitted[asset]` by ~1 wei (line 173) — negligible relative to total assets.
- Appends a new entry to the global FIFO queue via `_addUserWithdrawalRequest` (line 175).

The `ExceedAmountToWithdraw` check (line 170) does not block this because 1 wei committed is far below any realistic available balance.

**Unbounded iteration in `_unlockWithdrawalRequests`:**
The `while` loop (lines 790–814) iterates from `nextLockedNonce[asset]` to `firstExcludedIndex`. Each iteration performs a `keccak256`, two storage reads, arithmetic, and multiple storage writes. With N = 50,000 dust entries ahead of legitimate requests, the operator must either:
- Supply a large `firstExcludedIndex` and exhaust the block gas limit, or
- Supply a small `firstExcludedIndex` and issue hundreds of batched calls, each of which only advances `nextLockedNonce` through dust entries before reaching legitimate requests.

**FIFO enforcement blocks legitimate users:**
`completeWithdrawal` → `_processWithdrawalCompletion` checks (line 707):
```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```
Until `nextLockedNonce[asset]` advances past all dust entries, every legitimate request queued after them reverts with `WithdrawalLocked`. The rsETH transferred at `initiateWithdrawal` time (line 166) is held by the contract and cannot be recovered by the user.

## Impact Explanation
Legitimate users' rsETH is transferred to the contract at `initiateWithdrawal` time and is irrecoverable until `nextLockedNonce` advances past all preceding dust entries. During the period required to drain the dust queue (which may span many operator transactions and blocks), affected users cannot call `completeWithdrawal` successfully. This constitutes **temporary freezing of funds**, a Medium-severity impact in the allowed scope.

## Likelihood Explanation
- No privileged role is required; any rsETH holder can call `initiateWithdrawal`.
- `minRsEthAmountToWithdraw[asset]` is `0` by default and requires explicit admin action (`setMinRsEthAmountToWithdraw`) to be set; assets for which it is never configured remain permanently vulnerable.
- The attacker's rsETH is returned when each dust request is eventually processed, so the net cost is gas only — economically viable on low-fee networks or during low-congestion periods.
- The attack requires no flash loans, oracle manipulation, or privileged access and is straightforwardly repeatable.

**Likelihood: Medium.**

## Recommendation
1. **Enforce a non-zero minimum at initialisation:** Set a protocol-wide floor in `initialize()`:
```solidity
// e.g., in initialize():
minRsEthAmountToWithdraw[ETH_TOKEN] = 1e15;
```
2. **Reject zero in `setMinRsEthAmountToWithdraw`:**
```solidity
function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
    if (minRsEthAmountToWithdraw_ == 0) revert InvalidMinAmount();
    minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
    emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
}
```
3. **Cap per-call iterations in `_unlockWithdrawalRequests`:** Add a `maxIterations` hard cap so a single `unlockQueue` call processes at most N entries regardless of `firstExcludedIndex`, preventing gas exhaustion even if the queue is flooded.

## Proof of Concept
1. Deploy `LRTWithdrawalManager`; `minRsEthAmountToWithdraw[ETH_TOKEN]` is `0` (never set in `initialize()`). [1](#0-0) 
2. Attacker acquires rsETH (e.g., via `LRTDepositPool`) and calls `initiateWithdrawal(ETH_TOKEN, 1, "")` in a loop 50,000 times. Each call passes the guard at line 162 because `1 < 0` is false for `uint256`. [2](#0-1) 
3. Legitimate users call `initiateWithdrawal` with real amounts; their nonces are assigned after the 50,000 dust entries. [3](#0-2) 
4. Operator calls `unlockQueue(ETH_TOKEN, nextUnusedNonce, ...)`. The `while` loop in `_unlockWithdrawalRequests` iterates through all 50,000 dust entries before reaching legitimate requests, consuming far more than 30 M gas and reverting. [4](#0-3) 
5. Legitimate users call `completeWithdrawal`; the check at line 707 reverts with `WithdrawalLocked` because `nextLockedNonce[asset]` has not advanced past their nonces. Their rsETH (transferred at step 3 of their own flow) remains locked in the contract. [5](#0-4) 

**Foundry test sketch:**
```solidity
function testDustQueueDoS() public {
    // Attacker floods queue with 50_000 dust entries
    vm.startPrank(attacker);
    rsETH.approve(address(withdrawalManager), 50_000);
    for (uint256 i; i < 50_000; i++) {
        withdrawalManager.initiateWithdrawal(ETH_TOKEN, 1, "");
    }
    vm.stopPrank();

    // Legitimate user queues a real withdrawal
    vm.prank(legitimateUser);
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, 1 ether, "");

    // Advance blocks past withdrawal delay
    vm.roll(block.number + withdrawalDelayBlocks + 1);

    // Operator attempts to unlock the full queue — runs out of gas
    vm.prank(operator);
    vm.expectRevert(); // out of gas
    withdrawalManager.unlockQueue{gas: 30_000_000}(ETH_TOKEN, type(uint256).max, ...);

    // Legitimate user cannot complete withdrawal
    vm.prank(legitimateUser);
    vm.expectRevert(WithdrawalLocked.selector);
    withdrawalManager.completeWithdrawal(ETH_TOKEN, "");
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L90-98)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        withdrawalDelayBlocks = 8 days / 12 seconds;

        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L744-758)
```text
    function _addUserWithdrawalRequest(address asset, uint256 rsETHUnstaked, uint256 expectedAssetAmount) internal {
        uint256 nextUnusedNonce_ = nextUnusedNonce[asset];

        // Generate a unique identifier for the new withdrawal request.
        bytes32 requestId = getRequestId(asset, nextUnusedNonce_);

        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });

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
