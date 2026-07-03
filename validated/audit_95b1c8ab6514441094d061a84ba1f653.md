### Title
Token Decimal Precision Not Normalized in `viewSwapRsETHAmountAndFee` Causes Drastically Undercalculated rsETH Minted for Non-18-Decimal Deposits - (File: contracts/pools/RSETHPoolV3.sol)

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in all L2 pool contracts computes the rsETH amount to mint for a deposited ERC-20 token without normalizing the token's raw amount to 18-decimal precision. Because the oracle always returns a 1e18-precision rate, the formula silently treats a 6-decimal token amount (e.g., USDC) as if it were already in 1e18 scale, producing an rsETH output that is ~1e12 times smaller than correct. A user depositing a non-18-decimal token loses virtually all of their deposit value.

### Finding Description

Every L2 pool contract exposes a public `deposit(address token, uint256 amount, string referralId)` function that calls `viewSwapRsETHAmountAndFee(amount, token)` to determine how much rsETH/wrsETH to mint.

The ETH-deposit overload correctly normalizes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

The token-deposit overload does **not** normalize:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

The oracle wrapper `ChainlinkOracleForRSETHPoolCollateral.getRate()` always returns a value normalized to 1e18 regardless of the underlying feed's decimals:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [2](#0-1) 

So `tokenToETHRate` is always 1e18-precision. But `amountAfterFee` is in the token's **native** decimals. For a 6-decimal token like USDC:

| Variable | Value |
|---|---|
| `amountAfterFee` (1000 USDC) | `1_000_000_000` (1e9) |
| `tokenToETHRate` (USDC→ETH at $2500/ETH) | `4e14` |
| `rsETHToETHrate` | `1.05e18` |
| **Computed** `rsETHAmount` | `≈ 380_952` |
| **Correct** `rsETHAmount` | `≈ 3.8e17` |

The user receives ~1e12 times less rsETH than they are owed. The same bug is present identically in all five pool contracts: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation

**Critical — Direct theft of user funds.**

A user who calls `deposit(token, amount, referralId)` with a non-18-decimal token transfers their full token balance to the pool (which is then bridged to L1 and permanently absorbed into the protocol), but receives rsETH/wrsETH worth approximately `10^(18 - tokenDecimals)` times less than the fair value. For a 6-decimal token the shortfall is a factor of 1e12. The user has no mechanism to recover the deposited tokens because they hold almost no rsETH to redeem. This constitutes a permanent, near-total loss of the deposited principal.

### Likelihood Explanation

**Medium.** The currently deployed supported token is wstETH (18 decimals), for which the formula is coincidentally correct. However, the `addSupportedToken` admin function accepts any ERC-20 token and oracle pair with no decimal check. The protocol's architecture explicitly anticipates future token expansion (multiple `reinitialize` versions already add tokens). Any legitimate governance action to add a common LST or stablecoin with fewer than 18 decimals (e.g., USDC, USDT, WBTC) would immediately activate the bug for every subsequent depositor of that token. [7](#0-6) 

### Recommendation

Normalize `amountAfterFee` to 18-decimal precision before applying the rate ratio. Fetch the token's decimals and scale accordingly:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 amountNormalized = amountAfterFee * 1e18 / (10 ** tokenDecimals);
rsETHAmount = amountNormalized * tokenToETHRate / rsETHToETHrate;
```

Apply this fix consistently across all five pool contracts.

### Proof of Concept

1. Admin adds USDC (6 decimals) as a supported token with a `ChainlinkOracleForRSETHPoolCollateral` oracle pointing to the USDC/ETH Chainlink feed.
2. User calls `RSETHPoolV3.deposit(usdc, 1_000e6, "")` — depositing 1000 USDC (≈ 0.4 ETH at $2500/ETH, fair rsETH value ≈ 0.38e18).
3. `viewSwapRsETHAmountAndFee` computes:
   - `tokenToETHRate = 4e14` (oracle normalized to 1e18)
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1_000e6 * 4e14 / 1.05e18 ≈ 380_952`
4. User receives `380_952` wrsETH (≈ 3.8e-13 rsETH) instead of `≈ 3.8e17` wrsETH.
5. The 1000 USDC is bridged to L1 and absorbed by the protocol. The user's loss is ~100% of deposited value.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L330-334)
```text
        // rate of token in ETH
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
