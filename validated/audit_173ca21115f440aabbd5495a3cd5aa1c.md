### Title
Precision Loss in Token-to-rsETH Swap Calculation for Non-18-Decimal Tokens - (File: contracts/pools/RSETHPool.sol, RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolNoWrapper.sol)

---

### Summary

Multiple pool contracts compute the rsETH output for ERC-20 token deposits using the formula `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate`. This formula is missing a decimal normalization factor for the deposited token. For any token whose decimals differ from 18 (e.g., USDC with 6 decimals, WBTC with 8 decimals), the result is off by a factor of `1e18 / 10^tokenDecimals`, causing users to receive drastically fewer (or zero) rsETH while their tokens are permanently retained by the pool.

---

### Finding Description

The token-deposit path in `viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The oracle (`ChainlinkOracleForRSETHPoolCollateral.getRate()`) normalizes the Chainlink price to 1e18 precision:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [6](#0-5) 

So `tokenToETHRate` is always in 1e18 precision (ETH per 1 token unit). However, `amountAfterFee` is in the token's **native** decimal precision, not normalized to 1e18.

**Correct dimensional analysis:**

| Variable | Units |
|---|---|
| `amountAfterFee` | `token_units` (e.g., 1e6 for USDC) |
| `tokenToETHRate` | `ETH_wei / token_unit` (1e18 precision) |
| `rsETHToETHrate` | `ETH_wei / rsETH_unit` (1e18 precision) |

The correct formula is:
```
rsETHAmount = amountAfterFee * tokenToETHRate * 1e18 / (10^tokenDecimals * rsETHToETHrate)
```

The current formula omits the `1e18 / 10^tokenDecimals` scaling factor. For 18-decimal tokens this factor equals 1 (no error), but for USDC (6 decimals) the result is `1e12` times too small, and for WBTC (8 decimals) it is `1e10` times too small.

The ETH-deposit path correctly uses `amountAfterFee * 1e18 / rsETHToETHrate` because ETH itself has 18 decimals, making the scaling factor 1. The token path does not apply the equivalent normalization. [7](#0-6) 

---

### Impact Explanation

A user depositing 1,000 USDC (= `1000e6` in 6-decimal representation) when `tokenToETHRate ≈ 3.33e14` (≈ $3000/ETH) and `rsETHToETHrate ≈ 1.05e18`:

- **Expected rsETH:** `1000 USDC × (1/3000 ETH/USDC) / 1.05 ≈ 0.317 rsETH = 3.17e17`
- **Actual rsETH:** `1000e6 × 3.33e14 / 1.05e18 ≈ 3.17e5` (i.e., `3.17e5 / 1e18 ≈ 0 rsETH`)

The user's 1,000 USDC is transferred into the pool via `safeTransferFrom`, but they receive essentially zero rsETH. There is no `rsETHAmount == 0` guard in the deposit function. The tokens are permanently retained by the pool with no recourse for the user.

**Impact: Critical — direct theft/permanent loss of user funds.**

---

### Likelihood Explanation

The pool contracts expose a generic `supportedTokenOracle` mapping and `supportedTokenList` that allow any ERC-20 token to be added as a supported deposit asset. The `ChainlinkOracleForRSETHPoolCollateral` oracle wrapper is designed to work with any Chainlink feed regardless of the token's decimals. If any non-18-decimal token (USDC, WBTC, USDT, etc.) is configured as a supported token, every depositor using that token path is affected. The entry point is fully unprivileged — any user calling `deposit(token, amount, referralId)` triggers the vulnerable calculation. [8](#0-7) 

---

### Recommendation

Normalize `amountAfterFee` to 18 decimals before applying the rate formula, or include the token decimal scaling explicitly:

```solidity
// Normalize token amount to 1e18
uint256 amountAfterFeeNormalized = amountAfterFee * 1e18 / (10 ** IERC20Metadata(token).decimals());

// Calculate the final rsETH amount
rsETHAmount = amountAfterFeeNormalized * tokenToETHRate / rsETHToETHrate;
```

Alternatively, restructure as:
```solidity
rsETHAmount = amountAfterFee * tokenToETHRate * 1e18 / (rsETHToETHrate * 10 ** IERC20Metadata(token).decimals());
```

Apply this fix consistently across all affected pool contracts.

---

### Proof of Concept

**Setup:** USDC (6 decimals) added as a supported token with a Chainlink USDC/ETH oracle. ETH price = $3000, so `tokenToETHRate = 3.33e14`. `rsETHToETHrate = 1.05e18`.

**Attack path (unprivileged user):**
1. User calls `deposit(USDC, 1_000e6, "ref")` on `RSETHPoolV3`.
2. `IERC20(USDC).safeTransferFrom(user, pool, 1_000e6)` — 1000 USDC leaves user's wallet.
3. `viewSwapRsETHAmountAndFee(1_000e6, USDC)` computes:
   - `fee = 1_000e6 * feeBps / 10_000` (small)
   - `amountAfterFee ≈ 1_000e6`
   - `tokenToETHRate = 3.33e14`
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1_000e6 * 3.33e14 / 1.05e18 = 3.17e5`
4. `wrsETH.mint(user, 317000)` — user receives `317000 / 1e18 ≈ 0` rsETH.
5. User has lost 1000 USDC (~$1000) and received nothing of value.

The correct rsETH amount should be `≈ 3.17e17` (0.317 rsETH ≈ $950). The formula underestimates by a factor of `1e12`.

### Citations

**File:** contracts/pools/RSETHPool.sol (L311-319)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L343-346)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-292)
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
```

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L308-311)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-36)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```
