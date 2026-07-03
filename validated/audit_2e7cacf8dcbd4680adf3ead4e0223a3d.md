Audit Report

## Title
Newly Added Tokens in `RSETHPool` Have Zero Fee by Default, Allowing Depositors to Bypass Protocol Fees - (File: contracts/pools/RSETHPool.sol)

## Summary
`RSETHPool.addSupportedToken()` never initializes `tokenFeeBps[token]`, leaving it at the Solidity default of `0`. Any caller who invokes `deposit(token, amount, referralId)` before the admin separately calls `setTokenFeeBps()` receives rsETH with no fee deducted, permanently depriving the protocol of fee revenue that should have accrued to `feeEarnedInToken[token]`.

## Finding Description
`RSETHPool` maintains a per-token fee mapping at L88:

```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
```

`addSupportedToken()` (L637–656) stores the oracle and bridge for the new token but never writes to `tokenFeeBps[token]`:

```solidity
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
// tokenFeeBps[token] is never set — remains 0
emit AddSupportedToken(token, oracle, bridge);
```

`viewSwapRsETHAmountAndFee(amount, token)` (L335–336) reads this mapping directly:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
```

With `feeBpsForToken == 0`, `fee` is always `0`, and `deposit()` (L284–305) — which is open to any caller — transfers the full rsETH equivalent with no fee deducted and leaves `feeEarnedInToken[token]` at `0`. The only remedy is a separate `DEFAULT_ADMIN_ROLE` call to `setTokenFeeBps()` (L583–594), which has no atomicity guarantee with `addSupportedToken()`. No existing modifier (`whenNotPaused`, `onlySupportedToken`, `nonReentrant`) checks whether a fee has been configured.

## Impact Explanation
**High — Theft of unclaimed yield.** Every deposit made while `tokenFeeBps[token] == 0` accrues zero fee to `feeEarnedInToken[token]`. The protocol permanently loses fee revenue on those deposits, and depositors receive more rsETH than they are entitled to at the protocol's expense. The loss is irreversible once the deposits are processed.

## Likelihood Explanation
The window opens every time `addSupportedToken()` is executed. Because `addSupportedToken` is behind `TIMELOCK_ROLE`, the pending transaction is publicly visible on-chain before execution, giving any chain-monitoring actor advance notice. After execution, the window remains open until the admin separately calls `setTokenFeeBps()`. A sophisticated depositor can front-run or immediately follow the `addSupportedToken` transaction with a large deposit. This scenario is realistic and repeatable for every new token onboarding.

## Recommendation
Accept a `_feeBps` parameter in `addSupportedToken()` and set `tokenFeeBps[token]` atomically:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    ...
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    tokenFeeBps[token] = _feeBps;
    ...
}
```

This eliminates the zero-fee window entirely and mirrors how `feeBps` is set at initialization for ETH deposits.

## Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` remains `0`.
2. Attacker calls `deposit(wstETH, 1_000e18, "")`.
3. `viewSwapRsETHAmountAndFee(1_000e18, wstETH)` computes `fee = 1_000e18 * 0 / 10_000 = 0`.
4. Attacker receives rsETH equivalent to the full `1_000e18` wstETH; `feeEarnedInToken[wstETH]` stays `0`.
5. Admin later calls `setTokenFeeBps(wstETH, 30)` — the fee-free deposits already occurred and the protocol revenue is permanently lost.

**Foundry test sketch:**
```solidity
function test_zeroFeeWindowOnNewToken() public {
    vm.prank(timelockAdmin);
    pool.addSupportedToken(address(wstETH), oracle, bridge);
    // fee not yet set — tokenFeeBps[wstETH] == 0
    uint256 amount = 1_000e18;
    deal(address(wstETH), attacker, amount);
    vm.startPrank(attacker);
    wstETH.approve(address(pool), amount);
    pool.deposit(address(wstETH), amount, "");
    vm.stopPrank();
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0); // protocol collected nothing
}
```