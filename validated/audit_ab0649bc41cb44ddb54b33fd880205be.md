Audit Report

## Title
Daily Mint Limit Bypass via Day-Boundary Straddling — (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `limitDailyMint` modifier across all four L2 pool contracts uses a discrete integer day counter anchored to `startTimestamp` to reset `dailyMintAmount`. Because the reset fires at the exact second `(block.timestamp - startTimestamp) / 1 days` increments, any depositor can submit two deposits straddling that boundary and mint up to **2× `dailyMintLimit`** of wrsETH within two consecutive blocks, violating the protocol's stated per-day issuance ceiling.

## Finding Description
`getCurrentDay()` returns `(block.timestamp - startTimestamp) / 1 days`, which increments by exactly 1 at `startTimestamp + N * 86400`.

```solidity
// RSETHPoolV3.sol L110-124
uint256 currentDay = getCurrentDay();
if (currentDay > lastMintDay) {
    lastMintDay = currentDay;
    dailyMintAmount = 0;          // hard reset at exact 24-h boundary
}
if (dailyMintAmount + rsETHAmount > dailyMintLimit) revert DailyMintLimitExceeded();
dailyMintAmount += rsETHAmount;
```

The reset is a hard boundary: `dailyMintAmount` is zeroed the instant `currentDay` increments. There is no carry-over, rolling window, or sub-day accumulation. The next daily limit reset timestamp is publicly readable via `getNextDailyLimitResetTimestamp()` (`startTimestamp + (getCurrentDay() + 1) * 1 days`), making the exact target block fully deterministic.

**Exploit path:**

| Block | `block.timestamp` | `currentDay` | Action | `dailyMintAmount` after |
|-------|-------------------|--------------|--------|------------------------|
| A | `startTimestamp + N*86400 − 1` | N−1 = `lastMintDay` → no reset | `deposit(dailyMintLimit ETH)` | `dailyMintLimit` (cap hit) |
| B | `startTimestamp + N*86400` | N > `lastMintDay` → reset to 0 | `deposit(dailyMintLimit ETH)` | `dailyMintLimit` (cap hit again) |

Net result: **2 × `dailyMintLimit`** of wrsETH minted in two consecutive blocks. The attacker can repeat this every day boundary. No flash loan, oracle manipulation, or privileged role is required — only the ability to call the public `deposit()` function with sufficient ETH/LST.

The identical pattern is confirmed in all four pool variants:
- `RSETHPoolV3.sol` L110-124
- `RSETHPoolV3ExternalBridge.sol` L144-157
- `RSETHPoolV3WithNativeChainBridge.sol` L122-136
- `RSETHPoolV2ExternalBridge.sol` L111-125

The `whenNotPaused` and `nonReentrant` guards do not prevent two separate transactions in adjacent blocks. No other guard exists on the deposit path that would limit cross-boundary accumulation.

## Impact Explanation
The daily mint cap is the protocol's primary rate-limiting control on L2 wrsETH issuance. Bypassing it means the protocol fails to enforce its stated per-day issuance ceiling — a concrete security invariant. The attacker must supply real ETH/LST collateral, so no funds are directly stolen and the minted wrsETH is fully backed. This maps to **Low: Contract fails to deliver promised returns, but doesn't lose value**, as the protocol's promised rate-limiting guarantee is not delivered.

## Likelihood Explanation
The boundary timestamp is fully deterministic and publicly readable via `getNextDailyLimitResetTimestamp()`. Any depositor with sufficient ETH/LST can compute the exact target block and submit two back-to-back transactions (or a single bundle via a block builder on L2s that support it). No special role, flash loan, oracle access, or social engineering is required. The attack is repeatable every 24 hours. Likelihood is **Medium**.

## Recommendation
Replace the discrete day-counter reset with a rolling 24-hour window:

```solidity
// Track the timestamp of the last reset instead of a day index
uint256 public lastMintResetTimestamp;

modifier limitDailyMint(uint256 amount, address token) {
    if (block.timestamp < startTimestamp) revert MintBeforeStartTimestamp();

    if (block.timestamp >= lastMintResetTimestamp + 1 days) {
        dailyMintAmount = 0;
        lastMintResetTimestamp = block.timestamp;
    }

    uint256 rsETHAmount = /* calculate as before */;
    if (dailyMintAmount + rsETHAmount > dailyMintLimit) revert DailyMintLimitExceeded();
    dailyMintAmount += rsETHAmount;
    _;
}
```

This ensures the window always starts from the most recent reset, eliminating the exploitable hard boundary. Apply the fix to all four pool contracts.

## Proof of Concept
```
Setup:
  dailyMintLimit = 100 ETH worth of rsETH
  startTimestamp = T0
  lastMintDay = N-1 (day N-1 limit fully consumed)

Block A (timestamp = T0 + N*86400 - 1):
  getCurrentDay() = (T0 + N*86400 - 1 - T0) / 86400 = N-1 = lastMintDay
  → no reset
  deposit(100 ETH) → dailyMintAmount = 100  ✓ (limit hit)

Block B (timestamp = T0 + N*86400):
  getCurrentDay() = (T0 + N*86400 - T0) / 86400 = N > lastMintDay
  → dailyMintAmount reset to 0, lastMintDay = N
  deposit(100 ETH) → dailyMintAmount = 100  ✓ (limit hit again)

Result: 200 ETH of wrsETH minted in two consecutive blocks,
        double the intended 100 ETH/day cap.

Foundry test plan:
  1. Deploy RSETHPoolV3 with dailyMintLimit = 100e18.
  2. vm.warp(startTimestamp + N*86400 - 1); call deposit{value: 100e18}("").
  3. vm.warp(startTimestamp + N*86400);     call deposit{value: 100e18}("").
  4. Assert wrsETH.balanceOf(attacker) == 2 * expectedDailyMintAmount.
     (Both deposits succeed; no DailyMintLimitExceeded revert.)
```