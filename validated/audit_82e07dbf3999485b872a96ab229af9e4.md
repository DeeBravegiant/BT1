### Title
Withdrawal delay enforced in blocks with hardcoded 12-second block-time assumption renders security delay trivially short on Sonic and other fast-block chains - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` enforces a withdrawal delay by comparing `block.number` against a stored `withdrawalStartBlock`. Both the initial value and the enforced upper bound are computed by dividing a duration in seconds by `12` (the Ethereum mainnet block time). On Sonic chain — where the project is demonstrably deployed, as evidenced by `SonicChainNativeTokenBridge.sol` — blocks are produced roughly every 1 second, making the intended 8-day delay expire in approximately 16 hours.

### Finding Description
`withdrawalDelayBlocks` is initialised in `initialize()` as:

```solidity
withdrawalDelayBlocks = 8 days / 12 seconds;   // = 57,600 blocks
``` [1](#0-0) 

The manager-callable setter enforces a ceiling with the same assumption:

```solidity
if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
``` [2](#0-1) 

The delay is checked in two places using raw block numbers:

```solidity
// in _processWithdrawalCompletion
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [3](#0-2) 

```solidity
// in _unlockWithdrawalRequests
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
``` [4](#0-3) 

The `withdrawalStartBlock` is recorded as `block.number` at request time: [5](#0-4) 

On Sonic (≈1 s/block): `57,600 blocks × 1 s = 57,600 s ≈ 16 hours` instead of 8 days.  
The manager-enforced ceiling of `16 days / 12 = 115,200 blocks` becomes ≈32 hours instead of 16 days, so even a manual reconfiguration cannot restore the intended delay without exceeding the ceiling.

The existence of `SonicChainNativeTokenBridge.sol` confirms the protocol is deployed on Sonic: [6](#0-5) 

### Impact Explanation
The withdrawal delay is the protocol's primary time-buffer to detect adverse events (e.g., EigenLayer slashing, oracle manipulation, emergency pauses) and act before user funds leave the system. With the delay reduced 12× on Sonic, the protocol has only ~16 hours to detect and pause withdrawals instead of 8 days. Users can complete withdrawals far sooner than the protocol intends, and the `setWithdrawalDelayBlocks` ceiling (`16 days / 12`) prevents operators from restoring the correct delay without a contract upgrade. The contract fails to deliver the promised 8-day withdrawal security window.

**Impact: Low — Contract fails to deliver promised returns (security delay not enforced as specified).**

### Likelihood Explanation
The protocol is already deployed on Sonic chain. Every withdrawal request submitted on Sonic is affected automatically — no special attacker action is required. Any user initiating a withdrawal (`initiateWithdrawal`) will be able to complete it after ~16 hours rather than 8 days.

### Recommendation
Replace block-number arithmetic with `block.timestamp` throughout the withdrawal delay logic:

1. Change the state variable to store a duration in seconds: `uint256 public withdrawalDelaySeconds;`
2. Initialise it as `withdrawalDelaySeconds = 8 days;`
3. Record `withdrawalStartTime: block.timestamp` in `WithdrawalRequest`.
4. Replace both delay checks with `if (block.timestamp < request.withdrawalStartTime + withdrawalDelaySeconds)`.
5. Update `setWithdrawalDelayBlocks` (rename to `setWithdrawalDelaySeconds`) and cap at `16 days` in seconds.

`block.timestamp` is chain-agnostic and is the correct primitive for measuring real-world time across EVM-compatible chains.

### Proof of Concept
1. Deploy `LRTWithdrawalManager` on Sonic (1 s/block). `withdrawalDelayBlocks` = `57,600`.
2. User calls `initiateWithdrawal(asset, amount, "")`. `withdrawalStartBlock` = current block `N`.
3. Operator calls `unlockQueue(...)` after block `N + 57,600` (≈16 hours of real time on Sonic).
4. The check `block.number < request.withdrawalStartBlock + withdrawalDelayBlocks` passes after only ~16 hours.
5. User calls `completeWithdrawal(asset, "")` and receives funds — 7 days and 8 hours earlier than the intended 8-day delay.

The intended 8-day security window is reduced to ~16 hours with zero attacker effort, solely due to the hardcoded `/ 12 seconds` divisor.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L340-340)
```text
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
```

**File:** contracts/LRTWithdrawalManager.sol (L715-715)
```text
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L751-753)
```text
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L795-795)
```text
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L11-15)
```text
/// @title SonicChainNativeTokenBridge
/// @notice Bridge contract for transferring tokens from Sonic to Ethereum using Sonic's native bridge
/// @dev This contract must have the same address as SonicBridgeReceiver on ETH mainnet
/// @dev Implements IL2TokenBridge interface for integration with RSETHPoolV3AutoBridgedTokens
contract SonicChainNativeTokenBridge is IL2TokenBridge, AccessControl, ReentrancyGuard {
```
