Audit Report

## Title
Uninitialized `minRsEthAmountToWithdraw` allows dust-flooding of the FIFO withdrawal queue, temporarily freezing legitimate user withdrawals — (File: `contracts/LRTWithdrawalManager.sol`)

## Summary

`minRsEthAmountToWithdraw[asset]` defaults to `0` for every asset because `initialize()` never sets it, causing the only effective guard in `initiateWithdrawal()` to be `rsETHUnstaked == 0`. Any rsETH holder can flood the FIFO queue with arbitrarily many 1-wei withdrawal requests. Because `_unlockWithdrawalRequests()` advances `nextLockedNonce` strictly in order and `completeWithdrawal()` reverts with `WithdrawalLocked` until a request's nonce is below `nextLockedNonce`, legitimate users queued after the dust entries cannot complete their withdrawals until the operator drains every preceding dust entry across many expensive transactions.

## Finding Description

**Root cause — mapping never initialized:**

`initialize()` sets only `withdrawalDelayBlocks` and `lrtConfig`; `minRsEthAmountToWithdraw` is left at the Solidity default of `0` for every asset. [1](#0-0) 

**Guard collapse in `initiateWithdrawal()`:**

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

When `minRsEthAmountToWithdraw[asset] == 0`, the sub-expression `rsETHUnstaked < 0` is always `false` for `uint256`, so the check reduces to `rsETHUnstaked == 0`. Any value ≥ 1 wei is accepted. [2](#0-1) 

**Strict FIFO ordering in `_unlockWithdrawalRequests()`:**

The loop uses `break` (not `continue`) on every blocking condition, so `nextLockedNonce` cannot advance past any entry that has not yet been processed. A legitimate request at nonce `N` is unreachable until all nonces `0 … N-1` have been unlocked. [3](#0-2) 

**`completeWithdrawal()` enforces the lock:**

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

The user's funds are inaccessible until the operator has drained all preceding entries. [4](#0-3) 

**`setMinRsEthAmountToWithdraw` accepts zero — no remediation path without code change:**

Even if an admin attempts to set a minimum, the setter has no zero-check guard, so it can be silently reset to `0`. [5](#0-4) 

**`minAmountToDeposit` also defaults to `0`:**

`LRTDepositPool.initialize()` never sets `minAmountToDeposit`, and `_beforeDeposit()` applies the same collapsing guard (`depositAmount == 0 || depositAmount < minAmountToDeposit`), allowing the attacker to acquire rsETH in 1-wei increments at negligible cost. [6](#0-5) [7](#0-6) 

## Impact Explanation

**Temporary freezing of funds (Medium).** Legitimate users whose `initiateWithdrawal()` calls are queued after the attacker's dust entries cannot call `completeWithdrawal()` — and therefore cannot recover their assets — until the operator processes every preceding dust entry. With `withdrawalDelayBlocks` already imposing an ~8-day baseline delay, inserting N dust entries ahead of a legitimate request extends the effective freeze by however many `unlockQueue` batches are required to drain N entries.

**Unbounded gas consumption (Medium).** The operator must call `unlockQueue()` repeatedly; total operator gas cost scales linearly with the number of dust entries the attacker creates. The attacker's cost (gas for deposits + `initiateWithdrawal` calls, plus temporary rsETH lock-up) is asymmetrically lower than the operator's remediation cost.

## Likelihood Explanation

Any address can obtain rsETH by calling `LRTDepositPool.depositETH()` or `depositAsset()`. Because `minAmountToDeposit` also defaults to `0`, rsETH can be acquired in arbitrarily small increments. The attacker's only ongoing cost is gas; their rsETH is eventually recoverable. No privileged access, oracle manipulation, or external dependency is required. The attack is repeatable: the attacker can continuously enqueue new dust entries to keep the queue clogged faster than the operator can drain it.

## Recommendation

1. **Set a non-zero default in `initialize()`**: For each supported asset, set `minRsEthAmountToWithdraw[asset]` to a sensible floor (e.g., equivalent to ~0.001 ETH worth of rsETH) during initialisation.
2. **Guard `setMinRsEthAmountToWithdraw` against zero**: Add `if (minRsEthAmountToWithdraw_ == 0) revert InvalidMinAmount();`, analogous to the guard in `KernelVaultETH.setMinDeposit()`.
3. **Guard `setMinAmountToDeposit` against zero** in `LRTDepositPool` for the same reason.

## Proof of Concept

```
// Precondition: minRsEthAmountToWithdraw[ETH_TOKEN] == 0 (default, never set)
//               minAmountToDeposit == 0 (default, never set)

1. Attacker calls LRTDepositPool.depositETH{value: 1 ether}(0, "")
   → receives ~1e18 rsETH

2. Attacker approves LRTWithdrawalManager to spend rsETH.

3. Attacker calls initiateWithdrawal(ETH_TOKEN, 1, "") × 1,000,000
   Each call: rsETHUnstaked=1, guard check: (1==0 → false) || (1<0 → false) → no revert
   → Nonces 0 … 999,999 filled with dust entries

4. Victim calls initiateWithdrawal(ETH_TOKEN, 1e18, "")
   → Queued at nonce 1,000,000

5. After withdrawalDelayBlocks pass, victim calls completeWithdrawal(ETH_TOKEN, "")
   → Reverts: WithdrawalLocked (nonce 1,000,000 >= nextLockedNonce[ETH_TOKEN] == 0)

6. Operator calls unlockQueue(ETH_TOKEN, firstExcludedIndex=B, ...) in batches
   Each batch processes B entries; must exhaust all 1,000,000 dust nonces before
   nextLockedNonce reaches 1,000,000 and the victim's request becomes completable.
   → Victim's funds are frozen for the entire drain period; operator bears all gas cost.

// Foundry fuzz test sketch:
// fuzz: N in [1, 10_000]
// assert: after N dust initiateWithdrawal calls followed by one legitimate call,
//         completeWithdrawal for the legitimate user reverts WithdrawalLocked
//         until unlockQueue has been called enough times to advance
//         nextLockedNonce[asset] past N.
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

**File:** contracts/LRTWithdrawalManager.sol (L330-333)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
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

**File:** contracts/LRTDepositPool.sol (L45-52)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```
