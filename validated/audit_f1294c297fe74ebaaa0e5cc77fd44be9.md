### Title
Decimal Scaling Mismatch in Token-to-rsETH Swap Calculation Causes Severe Under-Minting for Non-18-Decimal Tokens - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in the L2 pool contracts computes the rsETH output amount using raw token units without normalizing to 18 decimals. Because the oracle's `getRate()` returns the price of one human-readable token unit in ETH (scaled to 1e18), but `amountAfterFee` is expressed in the token's native decimal precision, the division produces a result that is off by a factor of `10^(18 − tokenDecimals)`. For an 8-decimal token like WBTC this means the depositor receives `10^10` times fewer rsETH than owed, effectively confiscating their deposit. The same structural error is present in `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPool`.

### Finding Description

Every L2 pool contract exposes a token-deposit path:

```
deposit(address token, uint256 amount, string referralId)
  → viewSwapRsETHAmountAndFee(amount, token)
  → rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate
```

`tokenToETHRate` is fetched from `IOracle(supportedTokenOracle[token]).getRate()`. The canonical oracle wrapper is `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18
                          / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
return normalizedPrice;
```

This returns the price of **one human-readable token** (i.e., `10^tokenDecimals` smallest units) in ETH, expressed in 1e18 scale. For WBTC/ETH it returns `~15e18` meaning "15 ETH per 1 WBTC (= 1e8 WBTC units)".

The pool formula then computes:

```
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate
```

For a deposit of 1 WBTC (`amountAfterFee = 1e8`):

```
rsETHAmount = 1e8 * 15e18 / 1e18 = 15e8
```

The depositor should receive `15e18` rsETH (15 rsETH, since rsETH has 18 decimals). They receive `15e8` instead — a factor of `10^10` too small. The depositor's WBTC is transferred in full but they receive a negligible rsETH balance, constituting a near-total loss of deposited value.

The inverse function `viewSwapAssetToPremintedRsETH` has the symmetric error in the opposite direction:

```solidity
tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
```

For 15 rsETH (`rsETHAmount = 15e18`) → WBTC:

```
tokenAmount = 15e18 * 1e18 / 15e18 = 1e18
```

Expected: `1e8` WBTC units (1 WBTC). Actual: `1e18` WBTC units (`10^10` WBTC). This path drains the pool.

### Impact Explanation

**Critical — Direct theft of user funds / protocol insolvency.**

Any user who calls `deposit(token, amount, referralId)` with a supported non-18-decimal token receives `10^(18−decimals)` times fewer rsETH than the value they deposited. Their collateral is permanently held by the pool while their rsETH balance is negligible. For the reverse swap path, a caller can extract `10^(18−decimals)` times more tokens than the rsETH they supply, draining the pool's token reserves entirely.

### Likelihood Explanation

The pool contracts are explicitly designed to support arbitrary ERC-20 tokens via `addSupportedToken`. The `supportedTokenOracle` mapping and `supportedTokenList` array are generic. WBTC (8 decimals) and USDC/USDT (6 decimals) are natural candidates for future collateral additions on L2 chains. No code-level guard prevents adding a non-18-decimal token. Once any such token is added by an operator (a routine governance action, not a compromise), every depositor using that token is immediately affected.

### Recommendation

Normalize `amountAfterFee` to 18 decimals before applying the rate ratio. Retrieve the token's decimals and scale accordingly:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 1e18 / 10**tokenDecimals;
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Apply the symmetric inverse normalization in `viewSwapAssetToPremintedRsETH`. The same fix must be applied to all pool variants: `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`.

### Proof of Concept

1. Deploy a pool variant (e.g., `RSETHPoolV3`) with a WBTC oracle (`ChainlinkOracleForRSETHPoolCollateral` wrapping the WBTC/ETH Chainlink feed, returning `~15e18`).
2. Call `addSupportedToken(WBTC, wbtcOracle)`.
3. Approve and call `deposit(WBTC, 1e8, "")` (1 WBTC).
4. Observe: `viewSwapRsETHAmountAndFee(1e8, WBTC)` returns `rsETHAmount = 1e8 * 15e18 / rsETHToETHrate ≈ 15e8`.
5. Expected rsETH minted: `15e18`. Actual: `15e8`. Loss factor: `10^10`.
6. The depositor's 1 WBTC is transferred to the pool; they receive `0.0000000015` rsETH.

Root cause line: [1](#0-0) 

Same pattern in all pool variants: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Oracle normalization (confirms `getRate()` is in 1e18 scale per human-readable token, not per smallest unit): [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L311-311)
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

**File:** contracts/pools/RSETHPool.sol (L346-346)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-36)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```
