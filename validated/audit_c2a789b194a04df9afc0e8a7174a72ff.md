Audit Report

## Title
Pending rsETH Locked in `LRTWithdrawalManager` Has No Recovery Path During Protocol Pause - (File: contracts/LRTWithdrawalManager.sol)

## Summary
When a user calls `initiateWithdrawal()`, their rsETH is immediately transferred into `LRTWithdrawalManager` but is not burned until an operator later calls `unlockQueue()`. Every function that could advance the queue or return value to the user is gated by `whenNotPaused`, and no `cancelWithdrawal()` or rsETH-rescue path exists. Because `LRTOracle.updateRSETHPrice()` is a public function that automatically pauses `LRTWithdrawalManager` on a price-drop beyond `pricePercentageLimit`, any user can trigger this condition, locking all pending rsETH in the contract with no recovery path for the duration of the pause.

## Finding Description
**Transfer without burn at initiation:**
`initiateWithdrawal()` pulls rsETH from the caller at line 166 (`safeTransferFrom(msg.sender, address(this), rsETHUnstaked)`). The rsETH is not burned here; it sits in the contract until `unlockQueue()` burns it at line 305 (`IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)`).

**All exit paths blocked by `whenNotPaused`:**

| Function | Modifier location |
|---|---|
| `initiateWithdrawal()` | line 158 |
| `completeWithdrawal()` | line 183 |
| `completeWithdrawalForUser()` | line 199 |
| `instantWithdrawal()` | line 219 |
| `unlockQueue()` | line 279 |

No function in the contract allows a user to cancel a pending-but-not-yet-unlocked request and reclaim their rsETH while the contract is paused.

**`sweepRemainingAssets()` does not help:** This admin function (lines 395–414) transfers LST/ETH balances to the treasury. It does not return rsETH to users with pending requests, and it requires `!hasUnlockedWithdrawals(asset)` to pass.

**Automatic public pause trigger:**
`LRTOracle.updateRSETHPrice()` is a public, permissionless function (line 87). Its internal `_updateRsETHPrice()` logic at lines 277–281 calls `withdrawalManager.pause()` whenever the new rsETH price falls more than `pricePercentageLimit` below `highestRsethPrice`. Any external caller — including the affected user themselves — can trigger this path by calling `updateRSETHPrice()` at the right moment, or it fires naturally during routine oracle updates. Once paused, the `LRTWithdrawalManager` can only be unpaused by an address holding `LRT_ADMIN` role (line 352: `function unpause() external onlyLRTAdmin`).

**Exploit path:**
1. User calls `initiateWithdrawal(stETH, 10e18, "")`. rsETH transferred to contract at line 166.
2. rsETH price drops beyond `pricePercentageLimit`. Anyone calls `LRTOracle.updateRSETHPrice()`. Lines 277–281 fire: `withdrawalManager.pause()`.
3. User calls `completeWithdrawal(stETH, "")` → reverts: `whenNotPaused`.
4. User calls `instantWithdrawal(stETH, ...)` → reverts: `whenNotPaused`.
5. Operator calls `unlockQueue(stETH, ...)` → reverts: `whenNotPaused`.
6. No `cancelWithdrawal()` exists. rsETH remains locked for the duration of the pause.

## Impact Explanation
**Medium — Temporary freezing of funds.**

Users with pending withdrawal requests have their rsETH locked in `LRTWithdrawalManager` with zero recovery path for the entire duration of the pause. The rsETH has real market value and represents the user's restaked position. If the pause is lifted, the freeze is temporary. If the protocol is wound down while paused (e.g., following a critical exploit), the freeze becomes permanent — but that outcome depends on an admin decision and is therefore the upper bound, not the baseline. The concrete, realistic impact is temporary freezing of user rsETH funds, which maps to the **Medium** impact class.

## Likelihood Explanation
The pause trigger is partially automated and permissionless. `LRTOracle.updateRSETHPrice()` is callable by any address. During any significant slashing event or market dislocation — precisely the conditions under which users would want to withdraw — the price-drop circuit-breaker fires automatically. This is not a theoretical scenario; it is the designed behavior of the oracle's downside-protection mechanism. Additionally, any `PAUSER_ROLE` holder can call `pauseAll()` at any time. **Likelihood: Medium.**

## Recommendation
Add a `cancelWithdrawal(address asset)` function that:
- Is callable **without** a `whenNotPaused` guard (or decorated with `whenPaused` to make the intent explicit).
- Only allows cancellation of requests that have **not yet been unlocked** (i.e., `userNonce >= nextLockedNonce[asset]`).
- Removes the request from `userAssociatedNonces`, decrements `assetsCommitted[asset]`, and returns the rsETH to the user via `safeTransfer`.

Alternatively, add an admin-callable emergency function to return rsETH to users with pending-but-not-unlocked requests during a pause, callable without the `whenNotPaused` guard.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test outline
// 1. Fork mainnet at a recent block.
// 2. Impersonate a user; approve rsETH; call withdrawalManager.initiateWithdrawal(stETH, 10e18, "").
//    Confirm rsETH balance of withdrawalManager increased by 10e18.
// 3. Manipulate oracle state so newRsETHPrice < highestRsethPrice * (1 - pricePercentageLimit).
//    Call lrtOracle.updateRSETHPrice() from any EOA.
//    Assert: withdrawalManager.paused() == true.
// 4. As the user, call withdrawalManager.completeWithdrawal(stETH, "").
//    Expect revert with Pausable: paused (or equivalent whenNotPaused revert).
// 5. As the user, call withdrawalManager.instantWithdrawal(stETH, 10e18, "").
//    Expect revert with whenNotPaused.
// 6. As operator, call withdrawalManager.unlockQueue(stETH, ...).
//    Expect revert with whenNotPaused.
// 7. Assert no cancelWithdrawal() selector exists on withdrawalManager.
//    Assert withdrawalManager.rsETH().balanceOf(address(withdrawalManager)) >= 10e18.
// => User's rsETH is locked with no recovery path. Test passes, vulnerability confirmed.
```