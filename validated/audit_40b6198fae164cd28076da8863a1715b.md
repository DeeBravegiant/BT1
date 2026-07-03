Audit Report

## Title
Token Decimal Precision Not Normalized in `viewSwapRsETHAmountAndFee` Causes Drastically Undercalculated rsETH Minted for Non-18-Decimal Deposits - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in all five L2 pool contracts multiplies `amountAfterFee` (in the token's native decimals) directly by a 1e18-normalized oracle rate, without first scaling the amount to 18-decimal precision. For any token with fewer than 18 decimals, the computed rsETH output is `10^(18 - tokenDecimals)` times smaller than the correct value. A depositor of a 6-decimal token (e.g., USDC) would receive approximately 1e12 times less rsETH than owed while their full token balance is permanently transferred to the pool.

## Finding Description
In `RSETHPoolV3.sol`, the token-deposit overload of `viewSwapRsETHAmountAndFee` computes:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

`ChainlinkOracleForRSETHPoolCollateral.getRate()` always returns a value normalized to 1e18 precision, regardless of the underlying Chainlink feed's native decimals:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [2](#0-1) 

So `tokenToETHRate` is always 1e18-precision, but `amountAfterFee` is in the token's **native** decimals. For an 18-decimal token like wstETH, the formula is coincidentally correct. For a 6-decimal token like USDC, `amountAfterFee` is 1e12 times smaller than its 18-decimal equivalent, producing an rsETH output that is 1e12 times too small.

The `addSupportedToken` function performs no decimal check on the token being added: [3](#0-2) 

The identical bug exists in all five pool contracts: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

The deposit function transfers the full token amount from the user before computing and minting the (drastically undercalculated) rsETH: [8](#0-7) 

## Impact Explanation
**Critical — Direct theft of user funds.** A user depositing a non-18-decimal token transfers their full token balance to the pool (which is then bridged to L1 and permanently absorbed), but receives rsETH worth approximately `10^(18 - tokenDecimals)` times less than fair value. For USDC (6 decimals), the shortfall factor is 1e12, representing a near-total loss of deposited principal with no recovery mechanism. This matches the "Direct theft of any user funds, whether at-rest or in-motion" critical impact class.

## Likelihood Explanation
The currently deployed supported token is wstETH (18 decimals), for which the formula is coincidentally correct. However, the `addSupportedToken` admin function accepts any ERC-20 and oracle pair with no decimal validation. The protocol's architecture explicitly anticipates future token expansion — multiple `reinitialize` versions already add tokens. Any legitimate governance action to add a common LST or stablecoin with fewer than 18 decimals (e.g., USDC, USDT, WBTC) would immediately activate the bug for every subsequent depositor of that token. Once a non-18-decimal token is added, any unprivileged user calling `deposit(token, amount, referralId)` triggers the loss. The precondition is a normal, expected protocol operation, not a malicious or compromised governance action.

## Recommendation
Normalize `amountAfterFee` to 18-decimal precision before applying the rate ratio. Fetch the token's decimals and scale accordingly:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 amountNormalized = amountAfterFee * 1e18 / (10 ** tokenDecimals);
rsETHAmount = amountNormalized * tokenToETHRate / rsETHToETHrate;
```

Apply this fix consistently across all five pool contracts. Alternatively, enforce an 18-decimal requirement in `addSupportedToken` to prevent non-18-decimal tokens from being added at all.

## Proof of Concept
1. Admin calls `addSupportedToken(usdc, chainlinkUsdcEthOracle)` — USDC has 6 decimals, oracle returns `4e14` (USDC/ETH at $2500/ETH, normalized to 1e18).
2. User calls `RSETHPoolV3.deposit(usdc, 1_000e6, "")` — depositing 1000 USDC (fair rsETH value ≈ `3.8e17`).
3. `viewSwapRsETHAmountAndFee` computes:
   - `amountAfterFee ≈ 1_000e6` (assuming zero fee)
   - `tokenToETHRate = 4e14`
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1_000e6 * 4e14 / 1.05e18 ≈ 380_952`
4. User receives `380_952` wrsETH (≈ `3.8e-13` rsETH) instead of `≈ 3.8e17` wrsETH — a loss factor of ~1e12.
5. The 1000 USDC is bridged to L1 and absorbed by the protocol; the user holds negligible rsETH with no redemption path.

**Foundry fork test plan:** Deploy a fork of the pool, call `addSupportedToken` with a mock 6-decimal token and a mock oracle returning `4e14`, then call `deposit(token, 1000e6, "")` and assert `rsETHAmount < 1e6` (vs. expected `~3.8e17`). The assertion will pass, confirming the undercalculation.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L284-292)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-554)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-36)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L308-311)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L449-452)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L367-370)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L343-346)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
