Audit Report

## Title
`addSupportedToken` Never Initializes `tokenFeeBps`, Causing Zero Protocol Fees on All Token Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
`RSETHPool.sol` maintains a per-token fee mapping `tokenFeeBps` that is the sole input to the token-deposit fee calculation. The `addSupportedToken` function, which is the only way to register a new token, never sets `tokenFeeBps[token]`, leaving it at the Solidity default of `0`. Any user depositing a supported token will pay zero fees until the admin separately calls `setTokenFeeBps`, and there is no on-chain enforcement that this ever happens.

## Finding Description
`tokenFeeBps` is declared at line 88:
```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
```
It is consumed exclusively in `viewSwapRsETHAmountAndFee(uint256, address)` at lines 335–336:
```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
```
This view function is called directly by `deposit(address, uint256, string)` at line 298, which is a public, permissionless entry point.

`addSupportedToken` (lines 637–656) is gated by `TIMELOCK_ROLE` and is the only function that registers a new token. It sets `supportedTokenOracle[token]` and `tokenBridge[token]` but never writes to `tokenFeeBps[token]`. The separate `setTokenFeeBps` function (lines 583–594) is gated by `DEFAULT_ADMIN_ROLE` and is not called from `addSupportedToken`. There is no invariant, modifier, or check anywhere in the contract that prevents a deposit on a token whose `tokenFeeBps` is `0`.

Exploit path:
1. Admin calls `addSupportedToken(token, oracle, bridge)` — `tokenFeeBps[token]` remains `0`.
2. Any user calls `deposit(token, amount, referralId)`.
3. `viewSwapRsETHAmountAndFee` computes `fee = amount * 0 / 10_000 = 0`.
4. `feeEarnedInToken[token]` stays `0`; the user receives the full rsETH equivalent with no fee deducted.
5. Protocol collects zero fee revenue on all token deposits for that token, indefinitely or until admin separately calls `setTokenFeeBps`.

## Impact Explanation
The protocol's fee-collection mechanism for token deposits is silently non-functional for every newly added token. No user funds are lost or frozen, but the protocol fails to deliver its promised fee revenue on token deposits. This matches the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value."**

## Likelihood Explanation
The condition is reproduced automatically and unconditionally every time `addSupportedToken` is called — no attacker action is required to create it. Any ordinary depositor calling `deposit(token, amount, referralId)` during the window (or permanently if `setTokenFeeBps` is never called) triggers the zero-fee path. The likelihood is **High**.

## Recommendation
Add a `_feeBps` parameter to `addSupportedToken` and set `tokenFeeBps[token]` inside it, mirroring how `feeBps` is set during `initialize`:
```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    // ... existing checks ...
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    tokenFeeBps[token] = _feeBps;
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    emit AddSupportedToken(token, oracle, bridge);
}
```

## Proof of Concept
Foundry test sequence (no fork required):
1. Deploy `RSETHPool` and call `initialize` with a non-zero `feeBps`.
2. Call `addSupportedToken(mockToken, mockOracle, mockBridge)` as `TIMELOCK_ROLE`.
3. Assert `tokenFeeBps[mockToken] == 0`.
4. As an unprivileged EOA, call `deposit(mockToken, 1e18, "ref")`.
5. Assert `feeEarnedInToken[mockToken] == 0` and that the depositor received the full rsETH amount without any fee deduction.
6. Confirm the same result holds for any subsequent deposit until `setTokenFeeBps` is explicitly called by the admin.