### Title
Decimal mismatch in `viewSwapRsETHAmountAndFee` causes severe rsETH under-minting for non-18-decimal tokens - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in all L2 pool variants multiplies the raw token amount directly by the oracle rate without normalizing to 18 decimals. If any supported token has fewer than 18 decimals, depositors receive negligible rsETH while their tokens are permanently locked in the pool.

---

### Finding Description

In `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, and `RSETHPoolNoWrapper`, the rsETH mint amount for a token deposit is computed as:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` (from `IOracle(supportedTokenOracle[token]).getRate()`) and `rsETHToETHrate` (from `getRate()`) are both in 1e18 precision, as confirmed by `ChainlinkOracleForRSETHPoolCollateral.getRate()` which normalizes to 1e18:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

However, `amountAfterFee` is in the token's **native decimal precision**. For 18-decimal tokens (stETH, ETHx, rETH, swETH — all currently supported), the formula is correct. But the `addSupportedToken` function accepts any ERC20 token without decimal validation:

```solidity
function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
    ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    ...
}
```

If a 6-decimal token (e.g., USDC) is added, the formula produces:

```
rsETHAmount = 1000e6 * 1e18 / 1.05e18 ≈ 952e6
```

The user receives ~952e6 wei of rsETH (≈ 0.000000000000952 rsETH) instead of ~952e18 wei (≈ 952 rsETH). The deposited tokens are transferred into the pool but the user receives essentially zero rsETH — a 10^12× shortfall for a 6-decimal token.

The same structural flaw exists in the reverse direction in `viewSwapAssetToPremintedRsETH`:

```solidity
tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
```

For a 6-decimal token this produces a `tokenAmount` 10^12× larger than the pool's actual balance, causing every reverse-swap to revert.

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A user depositing a non-18-decimal token calls `deposit(token, amount, referralId)`. The pool takes custody of the full token amount via `safeTransferFrom`, then mints a negligible rsETH amount (effectively 0 for 6-decimal tokens). The user has no rsETH to redeem, and the tokens remain locked in the pool with no recovery path for the depositor. This constitutes direct, permanent loss of deposited funds.

---

### Likelihood Explanation

**Medium.** Adding a supported token requires `TIMELOCK_ROLE`, so it is not directly attacker-controlled. However:
- The protocol is explicitly designed to be multi-token extensible.
- No decimal validation exists in `addSupportedToken`.
- There is no on-chain guard preventing a 6-decimal token from being added.
- Once added, any unprivileged user calling `deposit(token, amount, referralId)` triggers the loss.

---

### Recommendation

Normalize `amountAfterFee` to 18 decimals before the rate multiplication in `viewSwapRsETHAmountAndFee`:

```diff
function viewSwapRsETHAmountAndFee(uint256 amount, address token)
    public view onlySupportedToken(token)
    returns (uint256 rsETHAmount, uint256 fee)
{
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;

+   uint8 tokenDecimals = IERC20Metadata(token).decimals();
+   if (tokenDecimals < 18) {
+       amountAfterFee = amountAfterFee * 10 ** (18 - tokenDecimals);
+   } else if (tokenDecimals > 18) {
+       amountAfterFee = amountAfterFee / 10 ** (tokenDecimals - 18);
+   }

    uint256 rsETHToETHrate = getRate();
    uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
    rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
}
```

Apply the symmetric inverse normalization in `viewSwapAssetToPremintedRsETH` so that `tokenAmount` is scaled back to the token's native decimals.

---

### Proof of Concept

The root cause is at the formula line in each pool variant: [1](#0-0) 

The oracle normalization to 1e18 is confirmed here (both `tokenToETHRate` and `rsETHToETHrate` are 1e18-scaled): [2](#0-1) 

The unrestricted `addSupportedToken` entry point (no decimal check): [3](#0-2) 

The same flaw is present in all pool variants: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-34)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L308-311)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
