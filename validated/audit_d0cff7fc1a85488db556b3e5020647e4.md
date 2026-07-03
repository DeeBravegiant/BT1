### Title
Token Decimal Mismatch in `viewSwapRsETHAmountAndFee` Produces Grossly Incorrect rsETH Amounts for Non-18-Decimal Collateral — (File: `contracts/pools/RSETHPoolV3.sol`)

---

### Summary

Every L2 pool contract that accepts ERC-20 token deposits computes the rsETH (or agETH) output with a formula that implicitly assumes the deposited token has 18 decimals. When a token with a different decimal count is added as supported collateral, the minted amount is off by a factor of `10^(18 − tokenDecimals)`, causing depositors to receive a negligible fraction of the correct rsETH amount (for tokens with fewer than 18 decimals) or a massively inflated amount (for tokens with more than 18 decimals).

---

### Finding Description

`viewSwapRsETHAmountAndFee(uint256 amount, address token)` in `RSETHPoolV3.sol` (and identically in `RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, and `AGETHPoolV3.sol`) computes:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

`tokenToETHRate` is sourced from `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
return normalizedPrice;
``` [2](#0-1) 

This oracle normalizes the Chainlink feed's own decimal precision to 1e18, but the resulting value represents the price of **one whole token** (e.g., 1 USDC) in ETH — it does **not** account for the token's own ERC-20 `decimals()`. Meanwhile, `amountAfterFee` is expressed in the token's raw smallest unit (e.g., `1e6` for 1 USDC).

The correct intermediate step — converting `amountAfterFee` from token units to a 1e18-normalized ETH value — is entirely absent:

```
// Missing: amountInETH = amountAfterFee * tokenToETHRate / 10^tokenDecimals
// Then:    rsETHAmount  = amountInETH * 1e18 / rsETHToETHrate
```

The same structural error appears in the reverse-swap path `viewSwapAssetToPremintedRsETH`:

```solidity
tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
``` [3](#0-2) 

`addSupportedToken` imposes no restriction on token decimals — any ERC-20 can be registered:

```solidity
function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
    ...
    if (IOracle(oracle).getRate() == 0) { revert UnsupportedOracle(); }
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
``` [4](#0-3) 

The same pattern is replicated across all pool variants: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) 

---

### Impact Explanation

**Tokens with fewer than 18 decimals (e.g., USDC = 6, USDT = 6, WBTC = 8):**

A depositor sending 1 USDC (`amountAfterFee = 1e6`) with `tokenToETHRate ≈ 3e14` and `rsETHToETHrate ≈ 1.05e18`:

```
rsETHAmount = 1e6 * 3e14 / 1.05e18 ≈ 285 wei
```

Correct value: `1e6 * 3e14 * 1e18 / 1e6 / 1.05e18 ≈ 2.85e14 wei`

The user receives `~285 wei` instead of `~2.85e14 wei` — losing **99.9999 %** of their deposited value. The surplus remains locked in the pool, constituting a permanent loss of user funds in motion.

**Tokens with more than 18 decimals (hypothetical):**

The formula produces an inflated rsETH amount, allowing a depositor to drain the pool's pre-minted rsETH inventory at a fraction of the true cost — direct theft of pool funds.

Impact classification: **Critical — direct theft of user funds / permanent freezing of user funds.**

---

### Likelihood Explanation

The `addSupportedToken` function is gated by `TIMELOCK_ROLE`, so the vulnerability is latent until a non-18-decimal token is registered. This is not an "admin compromise" scenario; it is a legitimate protocol governance action (expanding collateral types) that would inadvertently activate the bug. The pools are currently deployed on multiple L2 chains where USDC (6 decimals) and WBTC (8 decimals) are natural candidates for future collateral expansion. The formula contains no guard, comment, or NatSpec warning restricting supported tokens to 18-decimal assets.

Likelihood: **Low** (requires a governance action to add a non-18-decimal token, but no technical barrier prevents it and no documentation warns against it).

---

### Recommendation

Normalize `amountAfterFee` to 1e18 before applying the oracle rate, using the token's own `decimals()`:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

function viewSwapRsETHAmountAndFee(uint256 amount, address token)
    public view onlySupportedToken(token)
    returns (uint256 rsETHAmount, uint256 fee)
{
    uint256 feeBpsForToken = tokenFeeBps[token];
    fee = amount * feeBpsForToken / 10_000;
    uint256 amountAfterFee = amount - fee;

    uint8 tokenDecimals = IERC20Metadata(token).decimals();

    uint256 rsETHToETHrate = getRate();
    uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

    // Normalize amountAfterFee to 1e18 before applying the oracle rate
    rsETHAmount = amountAfterFee * tokenToETHRate * 1e18
                  / (rsETHToETHrate * 10 ** tokenDecimals);
}
```

Apply the symmetric correction to `viewSwapAssetToPremintedRsETH`. Alternatively, enforce at `addSupportedToken` time that only 18-decimal tokens may be registered, and document this invariant explicitly.

---

### Proof of Concept

**Setup:** Deploy `RSETHPoolV3` on an L2. Admin calls `addSupportedToken(USDC, chainlinkUSDCOracle)` via timelock. USDC has 6 decimals; the Chainlink USDC/ETH feed returns a price normalized to `~3e14` by `ChainlinkOracleForRSETHPoolCollateral`.

**Attack / Loss scenario:**

```
Alice calls deposit(USDC, 1_000_000 /* 1 USDC */, "ref")

viewSwapRsETHAmountAndFee(1_000_000, USDC):
  fee            = 1_000_000 * feeBps / 10_000  (small)
  amountAfterFee ≈ 1_000_000
  rsETHToETHrate ≈ 1.05e18
  tokenToETHRate ≈ 3e14   (price of 1 whole USDC in ETH, 1e18-normalized)

  rsETHAmount = 1_000_000 * 3e14 / 1.05e18
              = 3e20 / 1.05e18
              ≈ 285 wei of rsETH

Expected rsETHAmount:
  1 USDC = 0.0003 ETH
  0.0003 ETH / 1.05 ETH·rsETH⁻¹ = 0.000285 rsETH = 2.85e14 wei

Alice receives 285 wei instead of 2.85e14 wei.
Shortfall: 2.85e14 − 285 ≈ 2.85e14 wei (~99.9999% of value lost).
```

The deposited USDC is transferred to the pool but the minted rsETH is negligible, permanently trapping the economic value of the deposit inside the contract with no recovery path for Alice.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L399-401)
```text
        // Calculate the amount of token user will get for the amount of rsETH
        tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
    }
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

**File:** contracts/pools/RSETHPool.sol (L343-346)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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

**File:** contracts/agETH/AGETHPoolV3.sol (L191-194)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```
