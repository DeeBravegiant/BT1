Audit Report

## Title
Newly Added Tokens in RSETHPool Have Zero Protocol Fee by Default, Allowing Fee-Free Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
`addSupportedToken` never initializes `tokenFeeBps[token]`, leaving it at the Solidity default of `0`. Any deposit made between the `addSupportedToken` call and a subsequent `setTokenFeeBps` call pays zero protocol fees. The contract fails to collect fees it is designed to collect during this window.

## Finding Description
The `tokenFeeBps` mapping is declared at line 88 of `RSETHPool.sol`:

```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
```

`addSupportedToken` (lines 637–656) sets `supportedTokenOracle[token]` and `tokenBridge[token]` but never writes to `tokenFeeBps[token]`, leaving it at `0`. The token is immediately depositable via `deposit` (lines 284–305), which calls `viewSwapRsETHAmountAndFee(amount, token)` (lines 335–337):

```solidity
uint256 feeBpsForToken = tokenFeeBps[token]; // == 0
fee = amount * feeBpsForToken / 10_000;       // == 0
uint256 amountAfterFee = amount - fee;        // == amount
```

`setTokenFeeBps` (lines 583–594) is a completely separate function gated by `DEFAULT_ADMIN_ROLE` — a different role from the `TIMELOCK_ROLE` that controls `addSupportedToken` — with no coupling or ordering enforcement between the two calls. There is no guard in `deposit` or `viewSwapRsETHAmountAndFee` that requires a non-zero fee to have been configured.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The protocol is designed to collect per-token fees on deposits. During the window between `addSupportedToken` and `setTokenFeeBps`, the fee computation always yields `0` for any deposit amount, so `feeEarnedInToken[token]` is never incremented. The protocol treasury receives no fee revenue for those deposits. Depositor principal is not at risk.

## Likelihood Explanation
`addSupportedToken` is gated by `TIMELOCK_ROLE`, making the listing event publicly observable on-chain via the emitted `AddSupportedToken` event. Any user watching the chain can deposit immediately after the token is listed. The admin must issue a second, independent transaction (`setTokenFeeBps`) to close the window. Even a single-block delay on Arbitrum (where this contract is deployed) is sufficient to exploit the window. No privileged access, oracle manipulation, or external dependency is required — only a standard `deposit` call.

## Recommendation
Add a `feeBps` parameter to `addSupportedToken` and set `tokenFeeBps[token] = feeBps` atomically within the same function, before the token becomes depositable. Alternatively, add a check in `deposit` (or `viewSwapRsETHAmountAndFee`) requiring that `tokenFeeBps[token]` has been explicitly configured (e.g., via a separate boolean flag `tokenFeeConfigured[token]`), reverting if it has not.

## Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)`. `tokenFeeBps[wstETH]` remains `0`.
2. Alice observes the `AddSupportedToken` event on-chain.
3. Alice calls `deposit(wstETH, 1_000_000e18, "")`.
4. Inside `viewSwapRsETHAmountAndFee(1_000_000e18, wstETH)`:
   - `feeBpsForToken = tokenFeeBps[wstETH]` → `0`
   - `fee = 1_000_000e18 * 0 / 10_000` → `0`
   - `amountAfterFee = 1_000_000e18`
5. `feeEarnedInToken[wstETH] += 0` — no fee recorded.
6. Alice receives the full rsETH equivalent with zero fee paid.
7. Admin later calls `setTokenFeeBps(wstETH, 30)` — Alice's deposit already settled fee-free.

**Foundry test sketch:**
```solidity
function test_zeroFeeWindowOnTokenListing() public {
    vm.prank(timelockAdmin);
    pool.addSupportedToken(address(wstETH), oracle, bridge);
    // tokenFeeBps[wstETH] is 0 — setTokenFeeBps not yet called
    uint256 amount = 1_000_000e18;
    deal(address(wstETH), alice, amount);
    vm.prank(alice);
    wstETH.approve(address(pool), amount);
    vm.prank(alice);
    pool.deposit(address(wstETH), amount, "");
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0);
}
```