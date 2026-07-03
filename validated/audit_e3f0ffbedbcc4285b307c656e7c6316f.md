### Title
Low-Decimal Token Deposits Cause Near-Total Loss of User Funds Due to Missing Decimal Normalization in `viewSwapRsETHAmountAndFee` - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in multiple L2 pool contracts computes the rsETH/wrsETH output using a formula that implicitly assumes the deposited token has 18 decimals. When a low-decimal token (e.g., USDC with 6 decimals, WBTC with 8 decimals) is added as a supported token, the formula produces a result that is `10^(18 - tokenDecimals)` times smaller than correct, causing depositors to receive near-zero rsETH while their full token amount is transferred to the pool.

---

### Finding Description

The affected formula, present identically across five pool contracts, is:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` is sourced from `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which normalizes the Chainlink price feed answer to 1e18 precision and returns the price of **1 token unit** (in the token's native denomination) in ETH:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

For an 18-decimal token like stETH, `amountAfterFee` (in wei) and `tokenToETHRate` (price of 1 stETH in ETH, 1e18-scaled) are dimensionally consistent, so the formula is correct:

```
rsETHAmount = 1e18 (wei stETH) × 1e18 (ETH/stETH) / 1.05e18 (ETH/rsETH) ≈ 9.52e17 ✓
```

For a 6-decimal token like USDC (`tokenToETHRate ≈ 5e14`):

```
rsETHAmount = 1e6 (wei USDC) × 5e14 / 1.05e18 ≈ 476 wei rsETH
```

The correct output is:

```
(1e6 / 1e6) × 5e14 × 1e18 / 1.05e18 ≈ 4.76e14 wei rsETH
```

The formula under-mints by a factor of `10^(18 − 6) = 1e12`. For WBTC (8 decimals), the under-mint factor is `1e10`.

There is no `rsETHAmount > 0` guard in the `deposit` function, so for very small USDC amounts (< 2100 wei ≈ 0.0021 USDC), `rsETHAmount` rounds to exactly 0 and the user loses 100% of their deposit with no revert.

The same broken formula appears in:
- `RSETHPoolV3.sol` line 334
- `RSETHPoolNoWrapper.sol` line 311
- `RSETHPoolV3ExternalBridge.sol` line 452
- `RSETHPoolV3WithNativeChainBridge.sol` line 370
- `RSETHPool.sol` line 346

The inverse formula in `viewSwapAssetToPremintedRsETH` (`tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate`) is also broken in the opposite direction, over-paying by the same factor, enabling pool drain via the operator-restricted `swapAssetToPremintedRsETH`.

---

### Impact Explanation

**High — Theft of user funds.**

When a low-decimal token is added as a supported collateral, every depositor calling `deposit(token, amount, referralId)` transfers their full token balance to the pool but receives `10^(18 − tokenDecimals)` times less wrsETH/rsETH than the correct amount. The deposited tokens accumulate in the pool and are not recoverable by the user. For USDC this is a ~99.9999% loss per deposit; for WBTC it is ~99.9999999%. For dust-sized deposits the loss is 100% (rsETHAmount = 0, no revert).

---

### Likelihood Explanation

**Medium.**

The pool contracts expose `addSupportedToken` / `setSupportedTokenOracle` admin functions with no restriction on token decimals. The protocol is explicitly designed to be extensible to new collateral types. Adding USDC or WBTC as L2 pool collateral is a realistic operational decision. No malicious intent is required — a well-intentioned admin adding a low-decimal token triggers the bug for all subsequent depositors.

---

### Recommendation

1. **Normalize `amountAfterFee` to 18 decimals** before applying the formula:
   ```solidity
   uint8 tokenDecimals = IERC20Metadata(token).decimals();
   uint256 normalizedAmount = amountAfterFee * 10 ** (18 - tokenDecimals);
   rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
   ```
2. **Enforce 18-decimal tokens** in `addSupportedToken` / `setSupportedTokenOracle` by reverting if `IERC20Metadata(token).decimals() != 18`.
3. **Add a `rsETHAmount > 0` guard** in `deposit` to prevent silent total-loss deposits.

---

### Proof of Concept

**Setup**: Admin calls `addSupportedToken(USDC, chainlinkUSDCETHOracle)` on `RSETHPoolV3`. USDC has 6 decimals; the oracle returns `tokenToETHRate ≈ 5e14` (0.0005 ETH per USDC). `rsETHToETHrate ≈ 1.05e18`.

**Attack path** (unprivileged depositor):

1. User approves 2000 USDC (2000e6 wei) to `RSETHPoolV3`.
2. User calls `deposit(USDC, 2000e6, "")`.
3. `viewSwapRsETHAmountAndFee` computes:
   - `fee = 2000e6 * feeBps / 10_000` (e.g., 0 if feeBps = 0)
   - `rsETHAmount = 2000e6 * 5e14 / 1.05e18 = 1e21 / 1.05e18 ≈ 952` wei wrsETH
4. User receives 952 wei wrsETH ≈ 0.000000000000000952 wrsETH.
5. Correct amount: 2000 USDC ≈ 1 ETH ≈ 0.952 rsETH = 9.52e17 wei wrsETH.
6. **Loss: 99.9999999% of deposit value.** The 2000 USDC remain in the pool.

For a deposit of 1 wei USDC: `rsETHAmount = 1 * 5e14 / 1.05e18 = 0` → 100% loss, no revert.