Audit Report

## Title
Missing Token Decimal Normalization in `viewSwapRsETHAmountAndFee` Causes Near-Total Loss for Non-18-Decimal Token Depositors - (File: `contracts/pools/RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

## Summary

All five L2 RSETHPool variants compute rsETH output as `amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalizing `amountAfterFee` to 18 decimals. Because `ChainlinkOracleForRSETHPoolCollateral.getRate()` always returns a 1e18-precision price (ETH value of one whole token), the formula is only dimensionally correct for 18-decimal tokens. For any token with fewer decimals (e.g., 8 for WBTC/cbBTC, 6 for USDC), the minted rsETH amount is `10^(18 − decimals)` times smaller than correct, while the full token deposit is already transferred and held by the pool with no recovery path.

## Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` normalizes the Chainlink answer to 1e18 regardless of the feed's native decimals:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L34
uint256 normalizedPrice = uint256(ethPrice) * 1e18
    / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

This returns the ETH value of **one whole token** in 1e18 precision (e.g., `30e18` for 1 WBTC = 30 ETH).

Every pool variant then computes:

```solidity
// RSETHPool.sol L346 / RSETHPoolV3ExternalBridge.sol L452 / etc.
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

For an 18-decimal token, `amountAfterFee` is in 1e18 units, so the formula is dimensionally correct. For an 8-decimal token, `amountAfterFee` is in 1e8 units; multiplying by `tokenToETHRate` (1e18) gives a 1e26 intermediate, and dividing by `rsETHToETHrate` (1e18) yields a 1e8-precision result — exactly `10^10` times smaller than the correct 1e18-precision answer.

A grep across all pool contracts for `decimals` or `IERC20Metadata` returns zero matches — no normalization step exists anywhere in the deposit path. The `deposit()` function transfers the full `amount` from the user **before** calling `viewSwapRsETHAmountAndFee`, so the tokens are already gone when the near-zero rsETH is minted:

```solidity
// RSETHPool.sol L296-298 / RSETHPoolV3ExternalBridge.sol L403-405
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
// rsETHAmount is ~10^10x too small for 8-decimal tokens
```

The same structural flaw is present identically across all five pool variants at the cited lines.

## Impact Explanation

**Critical — direct theft of user funds.**

A depositor sending 1 WBTC (`amount = 1e8`, market value ≈ 30 ETH) receives approximately `2.94e8` rsETH (≈ `2.94 × 10^{-10}` rsETH in human units) instead of the correct `≈ 29.4e18` rsETH. The deposited WBTC is held by the pool and eventually bridged to L1; the user has no recourse. The loss ratio is `10^10 : 1`. This matches the allowed impact class: **Critical — direct theft of any user funds in motion**.

## Likelihood Explanation

**Medium.** The `supportedTokenOracle` mapping is admin-controlled, so a non-18-decimal token must first be listed via a governance action. However, this is a legitimate protocol operation — the contracts are explicitly designed to support multiple ERC-20 tokens, and the protocol's stated roadmap includes BTC-backed tokens. The admin listing WBTC or cbBTC is not a malicious or compromised action; it is the intended use of `addSupportedToken`. Once any such token is listed, every subsequent depositor of that token is immediately and silently harmed by calling the public `deposit(token, amount, referralId)` function. No attacker action is required beyond a normal deposit call.

## Recommendation

Normalize `amountAfterFee` to 18 decimals before applying the rate ratio in every `viewSwapRsETHAmountAndFee(uint256 amount, address token)` overload:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 1e18 / 10 ** tokenDecimals;
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

The fee calculation (`fee = amount * feeBps / 10_000`) and `feeEarnedInToken` accounting should remain in native token decimals; only the rsETH output conversion requires normalization. Apply this fix to all five pool variants at the cited lines.

## Proof of Concept

**Setup:** Admin lists WBTC (8 decimals) with a `ChainlinkOracleForRSETHPoolCollateral` pointing to the WBTC/ETH feed. WBTC/ETH = 30 → `tokenToETHRate = 30e18`. rsETH price = 1.02 ETH → `rsETHToETHrate = 1.02e18`. `feeBps = 0`.

**Call:** `deposit(WBTC, 1e8, "")` — depositing 1 WBTC.

**Execution:**
```
amountAfterFee = 1e8
rsETHAmount = 1e8 * 30e18 / 1.02e18
           = 3e27 / 1.02e18
           ≈ 2.94e8   ← minted to user
```

**Correct value:**
```
normalizedAmount = 1e8 * 1e18 / 1e8 = 1e18
rsETHAmount = 1e18 * 30e18 / 1.02e18 ≈ 29.4e18
```

**Foundry test plan:** Fork a chain where the pool is deployed. Impersonate the admin role, call `addSupportedToken(WBTC, chainlinkOracle)`. Impersonate a whale, approve and call `deposit(WBTC, 1e8, "")`. Assert `wrsETH.balanceOf(whale) < 1e10` (near-zero) and `IERC20(WBTC).balanceOf(pool) == 1e8` (full deposit held). Alternatively, a local unit test with a mock 8-decimal ERC-20 and a mock oracle returning `30e18` reproduces the exact arithmetic without a fork.