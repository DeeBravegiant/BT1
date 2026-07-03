Audit Report

## Title
Uninitialized `tokenFeeBps` in `addSupportedToken` Allows Fee-Free Deposits Until Admin Separately Configures Fee - (File: contracts/pools/RSETHPool.sol)

## Summary
`addSupportedToken` sets the oracle and bridge for a new token but never initializes `tokenFeeBps[token]`, leaving it at the Solidity default of `0`. Because `deposit` immediately becomes callable for any supported token and `viewSwapRsETHAmountAndFee` reads `tokenFeeBps[token]` directly to compute the fee, all deposits made between the `addSupportedToken` transaction and a subsequent `setTokenFeeBps` call pay zero protocol fees. The protocol fails to collect fee revenue it is designed to earn on every deposit.

## Finding Description
`tokenFeeBps` is declared as a mapping with no default initialization:

```solidity
// contracts/pools/RSETHPool.sol L88
mapping(address token => uint256 feeBps) public tokenFeeBps;
```

`addSupportedToken` (L637–656) sets `supportedTokenOracle[token]` and `tokenBridge[token]` but never writes to `tokenFeeBps[token]`. The `onlySupportedToken` modifier (L100–103) gates access solely on `supportedTokenOracle[token] != address(0)`, so the token is immediately depositable after `addSupportedToken` executes.

`viewSwapRsETHAmountAndFee(amount, token)` (L335–336) reads the uninitialized value:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token]; // == 0
fee = amount * feeBpsForToken / 10_000;       // == 0
```

`deposit(address token, uint256 amount, string referralId)` (L298–300) uses this result without any additional fee guard:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee; // += 0
```

`setTokenFeeBps` (L583–594) is a completely independent function requiring a separate `DEFAULT_ADMIN_ROLE` transaction. There is no coupling, ordering enforcement, or deposit gate between the two calls. The window is open for at least one block (and potentially longer on Arbitrum where admin multisig/timelock latency applies).

## Impact Explanation
The contract is designed to collect a per-token fee on every deposit. During the window between `addSupportedToken` and `setTokenFeeBps`, the fee computation always returns zero, so `feeEarnedInToken[token]` never increases. The protocol treasury receives no fee revenue for any deposit made in this window. No depositor funds are at risk; the contract simply fails to deliver the fee-collection behavior it promises. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
`addSupportedToken` is gated by `TIMELOCK_ROLE`, meaning the listing transaction is publicly visible on-chain before or at execution. Any user monitoring the chain can observe the `AddSupportedToken` event and immediately call `deposit`. On Arbitrum, block times are ~250 ms, so even a single-block delay between `addSupportedToken` and `setTokenFeeBps` is exploitable. The admin must issue a second, separate transaction to close the window, and there is no on-chain mechanism to enforce atomicity. Likelihood is **Medium**: the trigger is predictable and observable, and the cost to exploit is negligible.

## Recommendation
- **Short term:** Add a `feeBps` parameter to `addSupportedToken` and assign `tokenFeeBps[token] = feeBps` atomically within the same call, before the token becomes depositable.
- **Long term:** Add a guard in `deposit` (or `onlySupportedToken`) that reverts if `tokenFeeBps[token]` has never been explicitly set (e.g., require a separate boolean `tokenFeeConfigured[token]` flag, or require `tokenFeeBps[token] > 0` if zero-fee deposits are not a valid protocol state).

## Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)`. `tokenFeeBps[wstETH]` remains `0`.
2. Alice observes the `AddSupportedToken(wstETH, oracle, bridge)` event on-chain.
3. Alice calls `deposit(wstETH, 1_000_000e18, "")`.
4. Inside `viewSwapRsETHAmountAndFee(1_000_000e18, wstETH)`:
   - `feeBpsForToken = tokenFeeBps[wstETH]` → `0`
   - `fee = 1_000_000e18 * 0 / 10_000` → `0`
   - `amountAfterFee = 1_000_000e18`
5. `feeEarnedInToken[wstETH] += 0` — no fee recorded.
6. Alice receives the full rsETH equivalent of 1,000,000 wstETH with zero fee.
7. Admin later calls `setTokenFeeBps(wstETH, 30)` — Alice's deposit already settled fee-free.

**Foundry test sketch:**
```solidity
function test_zeroFeeBeforeSetTokenFeeBps() public {
    vm.prank(timelockAdmin);
    pool.addSupportedToken(address(wstETH), oracle, bridge);
    // tokenFeeBps[wstETH] is 0 — no setTokenFeeBps called yet
    uint256 amount = 1_000_000e18;
    deal(address(wstETH), alice, amount);
    vm.startPrank(alice);
    wstETH.approve(address(pool), amount);
    pool.deposit(address(wstETH), amount, "");
    vm.stopPrank();
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0); // fee never collected
}
```