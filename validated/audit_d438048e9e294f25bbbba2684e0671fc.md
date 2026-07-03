### Title
Precision Loss in Token-to-rsETH Conversion for Low-Decimal Tokens Causes Near-Total Fund Loss — (File: `contracts/pools/RSETHPoolV3.sol`)

---

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in every L2 pool variant computes the wrsETH output as `amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalizing `amountAfterFee` to 18-decimal precision. The oracle (`ChainlinkOracleForRSETHPoolCollateral`) returns the price of **one full token** in ETH at 1e18 precision, but `amountAfterFee` is in the token's **native decimal units**. For any token with fewer than 18 decimals (e.g., USDC at 6 decimals), the result is underestimated by a factor of 10^(18 − tokenDecimals), causing depositors to receive essentially zero wrsETH while their tokens are permanently taken by the pool.

---

### Finding Description

Every L2 pool variant contains the following token-deposit conversion:

```solidity
// RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolNoWrapper.sol
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

The `tokenToETHRate` is sourced from `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [2](#0-1) 

This oracle normalizes the Chainlink feed to 1e18 precision and returns the price of **one full token** (e.g., 1 USDC) in ETH. For USDC at $3,000/ETH, `tokenToETHRate ≈ 3.33e14`.

The formula `amountAfterFee * tokenToETHRate / rsETHToETHrate` is dimensionally correct only when `amountAfterFee` is already in 1e18 precision (i.e., the token has 18 decimals). For a 6-decimal token:

| Variable | Value |
|---|---|
| `amountAfterFee` (1 USDC) | `1e6` |
| `tokenToETHRate` | `≈ 3.33e14` |
| `rsETHToETHrate` | `≈ 1.05e18` |
| **Computed `rsETHAmount`** | `1e6 × 3.33e14 / 1.05e18 ≈ 317 wei` |
| **Correct `rsETHAmount`** | `1e18 × 3.33e14 / 1.05e18 ≈ 3.17e14 wei` |

The user receives **317 wei** of wrsETH instead of **3.17e14 wei** — a 10^12 underestimation. There is no `minRSETHAmountExpected` slippage guard in any pool deposit path, and no user-facing withdrawal function exists to recover the deposited tokens. [3](#0-2) [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A user depositing 1,000 USDC receives ≈ 317,000 wei of wrsETH (worth ~$3×10⁻¹³) instead of ≈ 3.17×10¹⁷ wei (worth ~$1,000). The USDC is irrecoverably taken from the user (no withdrawal path exists in the pool), bridged to L1, and deposited into the LRT protocol, increasing TVL and benefiting all rsETH holders at the depositor's expense. The loss is proportional to the decimal gap: 10^12 for 6-decimal tokens.

---

### Likelihood Explanation

**Low-to-Medium.** An admin must call `addSupportedToken` with a sub-18-decimal token and configure a Chainlink oracle for it. This is a legitimate operational action — the pool architecture explicitly supports multiple collateral token types with per-token oracles. An admin adding USDC or USDT (both 6 decimals, common L2 collateral) without auditing the decimal assumption in the formula would silently activate the bug for all subsequent depositors of that token.

---

### Recommendation

Normalize `amountAfterFee` to 18-decimal precision before the division, using the token's actual `decimals()`:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 10 ** (18 - tokenDecimals);
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Alternatively, enforce at `addSupportedToken` time that only 18-decimal tokens may be added, reverting otherwise.

---

### Proof of Concept

1. Admin calls `addSupportedToken(USDC, chainlinkUSDCOracle)` on `RSETHPoolV3`.
2. User calls `deposit(USDC, 1000e6, "ref")` — depositing 1,000 USDC.
3. Inside `viewSwapRsETHAmountAndFee(1000e6, USDC)`:
   - `fee = 1000e6 * feeBps / 10_000` (negligible)
   - `amountAfterFee ≈ 1000e6`
   - `tokenToETHRate = ChainlinkOracleForRSETHPoolCollateral.getRate()` → `≈ 3.33e14`
   - `rsETHToETHrate = getRate()` → `≈ 1.05e18`
   - `rsETHAmount = 1000e6 * 3.33e14 / 1.05e18 ≈ 317,000 wei`
4. `wrsETH.mint(msg.sender, 317_000)` — user receives 317,000 wei of wrsETH (≈ $3×10⁻¹³).
5. User's 1,000 USDC is held in the pool, bridged to L1, and absorbed into protocol TVL.
6. User has no mechanism to recover their USDC. [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L440-453)
```text
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L299-312)
```text
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
