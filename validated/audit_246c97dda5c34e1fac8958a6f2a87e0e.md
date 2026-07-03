Audit Report

## Title
Missing Decimal Normalization in `viewSwapRsETHAmountAndFee` Causes Near-Zero rsETH Minting for Non-18 Decimal Tokens - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

## Summary

The token deposit path in all three V3 pool contracts computes `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalizing `amountAfterFee` to 18 decimals first. Because `tokenToETHRate` from `ChainlinkOracleForRSETHPoolCollateral.getRate()` represents the price of one whole token scaled to 1e18, the formula is only dimensionally correct for 18-decimal tokens. For tokens with fewer decimals (e.g., wBTC at 8 decimals), a depositor's full token balance is transferred into the pool while they receive a factor of `1e(18 - tokenDecimals)` less rsETH than owed, constituting direct theft of depositor funds.

## Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` normalizes the Chainlink answer to 1e18 precision regardless of the collateral token's own decimals:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
return normalizedPrice;
``` [1](#0-0) 

This means `tokenToETHRate` = (price of 1 whole token in ETH) × 1e18, independent of the token's decimals.

The ETH deposit path correctly handles this because ETH is always 18 decimals:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [2](#0-1) 

The token deposit path in all three contracts uses:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [3](#0-2) [4](#0-3) [5](#0-4) 

For an 8-decimal token, `amountAfterFee` is in 1e8 units while `tokenToETHRate` encodes the price of 1e8 units (one whole token). The product `amountAfterFee * tokenToETHRate` is therefore 1e10 times smaller than the equivalent ETH-denominated value in wei, producing an rsETH amount 1e10 times too small. No decimal normalization exists anywhere in the deposit or view functions — confirmed by the absence of any `decimals()` call in the pool contracts outside the oracle wrapper.

The exploit path requires no privilege: `deposit(address token, uint256 amount, string referralId)` is a public function callable by any user. [6](#0-5) 

The full token amount is transferred in via `safeTransferFrom`, then `viewSwapRsETHAmountAndFee(amount, token)` is called with the raw token-decimal amount, and the resulting near-zero rsETH is minted. The depositor's tokens are permanently locked in the pool with no recourse.

## Impact Explanation

**Critical — Direct theft of user funds.** A depositor of 1 wBTC (1e8 units, ~20 ETH value) with `rsETHToETHrate = 1.05e18` and `tokenToETHRate = 20e18` receives `≈ 19.05e8` rsETH instead of `≈ 19.05e18` rsETH — a factor of 1e10 shortfall. The depositor's wBTC is fully transferred into the pool; the unaccounted value accrues to all existing rsETH holders. For tokens with more than 18 decimals the formula over-mints rsETH, causing protocol insolvency. Both outcomes fall within the allowed Critical impact class.

## Likelihood Explanation

The `supportedTokenOracle` mapping accepts any token address set by an admin with no on-chain enforcement of 18 decimals. The protocol is explicitly designed to be extensible to new collateral types. Any legitimate admin addition of a non-18 decimal token (wBTC, USDC, USDT) immediately activates the bug for every subsequent depositor of that token. The triggering action (depositing a supported token) requires no privilege and is the normal intended user flow. Likelihood is Medium given the dependency on an admin first adding such a token, but the impact upon activation is immediate and affects all depositors of that token.

## Recommendation

Normalize `amountAfterFee` to 18 decimals before applying the rate ratio in all three contracts:

```solidity
uint256 tokenDecimals = IERC20Metadata(token).decimals();
uint256 amountIn18 = amountAfterFee * 1e18 / 10 ** tokenDecimals;
rsETHAmount = amountIn18 * tokenToETHRate / rsETHToETHrate;
```

This mirrors the ETH path where `amountAfterFee` is already in 1e18 units and the `1e18` multiplier is explicit.

## Proof of Concept

Deploy a mock ERC-20 with 8 decimals and a mock oracle returning `20e18`. Register it as a supported token. Call `deposit(token, 1e8, "")` from an unprivileged address. Observe that `wrsETH.balanceOf(depositor)` equals `≈ 19.05e8` instead of `≈ 19.05e18`. The depositor's 1e8 token units are held by the pool contract while the minted rsETH is negligible.

Foundry test outline:
1. Fork or deploy `RSETHPoolV3` with a mock `wrsETH` and mock `getRate()` returning `1.05e18`.
2. Deploy `MockERC20` with `decimals() = 8`; deploy `MockOracle` with `getRate() = 20e18`.
3. Admin calls `addSupportedToken(mockToken, mockOracle)`.
4. Mint `1e8` mock tokens to attacker; attacker approves pool.
5. Attacker calls `deposit(mockToken, 1e8, "ref")`.
6. Assert `wrsETH.balanceOf(attacker) ≈ 19.05e8` (actual) vs expected `≈ 19.05e18`.
7. Assert pool holds `1e8` mock tokens with no corresponding rsETH backing.

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-36)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L307-307)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L452-452)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L370-370)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
