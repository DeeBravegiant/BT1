Audit Report

## Title
Zero `tokenFeeBps` on Newly Added Tokens Allows Fee-Free Deposits - (`contracts/pools/RSETHPool.sol`)

## Summary
`RSETHPool.addSupportedToken` sets the oracle and bridge for a new token but never initialises `tokenFeeBps[token]`, leaving it at the Solidity default of `0`. Any depositor can call the public `deposit(token, amount, referralId)` function immediately after the token is added and receive rsETH calculated on the full deposit amount with zero fee deducted. The fee revenue that should accrue to `feeEarnedInToken[token]` is permanently lost until a separate `setTokenFeeBps` call is made by the admin.

## Finding Description
`tokenFeeBps` is declared at line 88:
```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
```
`addSupportedToken` (lines 637–656) sets `supportedTokenOracle[token]` and `tokenBridge[token]` but never writes to `tokenFeeBps[token]`:
```solidity
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
// tokenFeeBps[token] is never set → remains 0
emit AddSupportedToken(token, oracle, bridge);
```
The public `deposit(address token, uint256 amount, string memory referralId)` function (lines 284–305) calls `viewSwapRsETHAmountAndFee(amount, token)` (lines 326–347), which reads `tokenFeeBps[token]` directly:
```solidity
uint256 feeBpsForToken = tokenFeeBps[token]; // == 0
fee = amount * feeBpsForToken / 10_000;      // == 0
uint256 amountAfterFee = amount - fee;        // == amount
```
Because `fee == 0`, `feeEarnedInToken[token] += 0` and the depositor receives rsETH for the full `amount`. The only remediation path is a separate `setTokenFeeBps` call (lines 583–594) by `DEFAULT_ADMIN_ROLE`, which may span multiple blocks or be omitted entirely. There are no guards in `deposit` or `viewSwapRsETHAmountAndFee` that prevent execution when `tokenFeeBps[token] == 0`.

## Impact Explanation
Every deposit made in the window between `addSupportedToken` and `setTokenFeeBps` pays zero protocol fee. The fee revenue that should have accrued to `feeEarnedInToken[token]` — and would ultimately be claimed by the protocol via `withdrawFees` — is permanently lost. This is a concrete, measurable loss of protocol yield and maps directly to the allowed impact **High: Theft of unclaimed yield**.

## Likelihood Explanation
`addSupportedToken` is a routine admin operation gated by `TIMELOCK_ROLE`. Because timelocks are observable on-chain before execution, any depositor — including an automated bot — can monitor the mempool and submit a `deposit` transaction immediately after (or in the same block as) `addSupportedToken`. The exploit does not require front-running exclusively; the window persists until `setTokenFeeBps` is called, which may span many blocks. The attack is repeatable on every new token addition and scales linearly with deposit size.

## Recommendation
Pass the initial fee basis points as a parameter to `addSupportedToken` and set it atomically:
```solidity
function addSupportedToken(address token, address oracle, address bridge, uint256 _feeBps)
    external onlyRole(TIMELOCK_ROLE)
{
    // ... existing checks ...
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    tokenFeeBps[token] = _feeBps;
    emit AddSupportedToken(token, oracle, bridge);
    emit TokenFeeBpsSet(token, _feeBps);
}
```
This eliminates the zero-fee window entirely.

## Proof of Concept
1. Admin calls `addSupportedToken(wstETH, wstETHOracle, wstETHBridge)` — `tokenFeeBps[wstETH]` is `0`.
2. Any user calls `deposit(wstETH, 1_000e18, "")`.
3. `viewSwapRsETHAmountAndFee(1_000e18, wstETH)` computes `fee = 1_000e18 * 0 / 10_000 = 0`.
4. `feeEarnedInToken[wstETH]` remains `0`; user receives rsETH for the full `1_000e18` with no fee.
5. Admin later calls `setTokenFeeBps(wstETH, 30)` — the fee on all prior deposits is permanently lost.

**Foundry test plan:**
```solidity
function test_zeroFeeWindowOnAddSupportedToken() public {
    vm.prank(timelockAdmin);
    pool.addSupportedToken(address(wstETH), address(oracle), address(bridge));

    // tokenFeeBps is 0 immediately after addition
    assertEq(pool.tokenFeeBps(address(wstETH)), 0);

    uint256 depositAmount = 1_000e18;
    deal(address(wstETH), attacker, depositAmount);
    vm.startPrank(attacker);
    wstETH.approve(address(pool), depositAmount);
    pool.deposit(address(wstETH), depositAmount, "");
    vm.stopPrank();

    // No fee accrued
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0);

    // Set fee after the fact — too late
    vm.prank(admin);
    pool.setTokenFeeBps(address(wstETH), 30);
    // Fee on attacker's deposit is permanently lost
}
```