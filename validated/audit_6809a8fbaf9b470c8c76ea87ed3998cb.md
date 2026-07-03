Audit Report

## Title
Uninitialized `tokenFeeBps` Defaults to Zero, Allowing Fee-Free Token Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
`addSupportedToken` registers a new token but never initializes `tokenFeeBps[token]`, leaving it at the Solidity mapping default of `0`. Any unprivileged depositor who calls `deposit(token, amount, referralId)` during the window between `addSupportedToken` and a subsequent `setTokenFeeBps` call receives wrsETH with zero protocol fees charged, permanently depriving the protocol treasury of fee revenue for those deposits.

## Finding Description
`addSupportedToken` (L637–656) sets `supportedTokenList`, `supportedTokenOracle[token]`, and `tokenBridge[token]`, but never writes to `tokenFeeBps[token]`:

```solidity
// L651-655
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
// tokenFeeBps[token] is never written — defaults to 0
emit AddSupportedToken(token, oracle, bridge);
```

`viewSwapRsETHAmountAndFee` (L335–336) reads directly from that mapping:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token]; // 0 for any new token
fee = amount * feeBpsForToken / 10_000;       // always 0
```

`deposit(token, amount, referralId)` (L298–300) then accumulates `feeEarnedInToken[token] += 0`, so no fee is ever accrued. The only remediation path is a separate admin call to `setTokenFeeBps` (L583–594), which is gated behind `DEFAULT_ADMIN_ROLE` and has no atomicity guarantee with `addSupportedToken`. The `onlySupportedToken` modifier (L100–103) only checks `supportedTokenOracle[token] != address(0)`, which is set by `addSupportedToken`, so deposits are immediately possible after listing.

Exploit path:
1. `TIMELOCK_ROLE` calls `addSupportedToken(tokenX, oracle, bridge)` — `tokenFeeBps[tokenX]` = 0.
2. Attacker observes `AddSupportedToken` event and calls `deposit(tokenX, largeAmount, "")`.
3. `viewSwapRsETHAmountAndFee` returns `fee = 0`.
4. Attacker receives full wrsETH equivalent of `largeAmount` with zero fee deducted.
5. `feeEarnedInToken[tokenX]` remains 0; protocol treasury receives nothing.
6. Steps 2–5 repeat until admin separately calls `setTokenFeeBps(tokenX, N)`.

No existing guard prevents this: `nonReentrant`, `whenNotPaused`, and `onlySupportedToken` all pass normally.

## Impact Explanation
The protocol's fee mechanism is its only compensation for providing the swap service. Every deposit during the zero-fee window results in the depositor receiving the full wrsETH value that should have been partially retained as protocol fees. This is a direct, concrete loss of fee revenue — yield that the protocol was designed to earn but cannot recover after the fact. Impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
Adding new supported tokens is a routine protocol operation. The window between `addSupportedToken` and `setTokenFeeBps` is non-zero in any realistic deployment sequence, as they are separate transactions (and `addSupportedToken` is timelocked while `setTokenFeeBps` is `DEFAULT_ADMIN_ROLE`). Any depositor monitoring the chain for `AddSupportedToken` events can immediately begin depositing at zero cost. No special privilege is required. The window can persist for an arbitrary duration and the attack is repeatable with any deposit size.

## Recommendation
Pass and store the fee atomically inside `addSupportedToken`:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    // ... existing checks ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    tokenFeeBps[token] = _feeBps; // initialize atomically
    emit AddSupportedToken(token, oracle, bridge);
}
```

This eliminates the window entirely by ensuring no deposit can occur before a fee is set.

## Proof of Concept
Foundry test outline:

```solidity
function test_zeroFeeWindowOnNewToken() public {
    // 1. Admin adds a new token (tokenFeeBps not set)
    vm.prank(timelockRole);
    pool.addSupportedToken(address(tokenX), address(oracle), address(bridge));

    // 2. Verify tokenFeeBps is 0
    assertEq(pool.tokenFeeBps(address(tokenX)), 0);

    // 3. Attacker deposits a large amount
    uint256 depositAmount = 1_000_000e18;
    vm.startPrank(attacker);
    tokenX.approve(address(pool), depositAmount);
    pool.deposit(address(tokenX), depositAmount, "");
    vm.stopPrank();

    // 4. Verify zero fee was accrued
    assertEq(pool.feeEarnedInToken(address(tokenX)), 0);

    // 5. Verify attacker received full wrsETH (no fee deducted)
    (uint256 expectedRsETH, uint256 fee) = pool.viewSwapRsETHAmountAndFee(depositAmount, address(tokenX));
    assertEq(fee, 0);
    assertEq(wrsETH.balanceOf(attacker), expectedRsETH);
}
```