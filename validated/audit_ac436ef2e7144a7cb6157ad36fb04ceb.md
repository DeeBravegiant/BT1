### Title
Missing Decimal Normalization in Token-to-rsETH Swap Calculation Causes Depositor Fund Loss - (File: contracts/pools/RSETHPool.sol, RSETHPoolV3.sol, RSETHPoolNoWrapper.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

All L2 pool contracts share a `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function that computes the wrsETH amount to mint for a deposited ERC-20 token. The formula multiplies the raw token amount (in the token's native decimal precision) by a Chainlink-derived rate that represents the price of **one whole token** in ETH (1e18 precision). For 18-decimal tokens this cancels correctly, but for any token with fewer than 18 decimals (e.g., WBTC at 8 decimals) the result is off by `10^(18 - tokenDecimals)`, causing the depositor to receive a negligible amount of wrsETH while their full token balance is transferred to the pool and bridged to L1.

---

### Finding Description

The vulnerable calculation appears identically across all L2 pool variants:

```solidity
// rate of token in ETH
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

// Calculate the final rsETH amount
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The oracle `ChainlinkOracleForRSETHPoolCollateral.getRate()` normalizes the Chainlink answer to 1e18 precision, yielding the price of **one whole token** in ETH:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [6](#0-5) 

For an 18-decimal token (e.g., wstETH), `amountAfterFee` is already in 1e18 units, so the formula is dimensionally consistent. For WBTC (8 decimals), `amountAfterFee` is in 1e8 units while `tokenToETHRate` still represents the price of 1 whole WBTC (≈15e18). The product is 1e10 times smaller than it should be.

**Concrete arithmetic for 1 WBTC deposit (≈15 ETH value):**

| Variable | Value |
|---|---|
| `amountAfterFee` | `1e8` (1 WBTC in native units) |
| `tokenToETHRate` | `15e18` (price of 1 whole WBTC in ETH) |
| `rsETHToETHrate` | `~1.05e18` |
| **Actual `rsETHAmount`** | `1e8 × 15e18 / 1.05e18 ≈ 1.43e9` (≈ 0 wrsETH) |
| **Expected `rsETHAmount`** | `1e18 × 15e18 / 1.05e18 ≈ 14.3e18` (≈ 14.3 wrsETH) |

The depositor receives `1e10` times less wrsETH than owed. Their WBTC is still transferred to the pool via `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` and subsequently bridged to L1, but the user holds no wrsETH to represent that value. [7](#0-6) 

The same root cause exists in `LRTDepositPool.getRsETHAmountToMint` and `LRTOracle._getTotalEthInProtocol` on L1, where `totalAssetAmt.mulWad(assetER)` also assumes 18-decimal token amounts: [8](#0-7) [9](#0-8) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A depositor who sends a non-18-decimal token (e.g., WBTC, USDC) to any L2 pool receives a negligible wrsETH balance (effectively zero). Their full token amount is transferred to the pool contract and bridged to L1, permanently increasing protocol TVL and benefiting existing rsETH holders, while the depositor is left with nothing. There is no recovery path: the pool has no refund mechanism, and the user holds no wrsETH to redeem.

---

### Likelihood Explanation

**Medium.** The vulnerability is latent in all L2 pool contracts. It activates the moment an admin calls `addSupportedToken` with any token whose `decimals() < 18`. This is a routine, legitimate admin operation — the admin may add WBTC or a stablecoin as a collateral option without being aware of the decimal assumption embedded in the formula. No malicious intent is required; the code silently produces the wrong result for any such token.

---

### Recommendation

Normalize `amountAfterFee` to 18 decimals before applying the rate formula:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 10 ** (18 - tokenDecimals);
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Apply the same fix to `LRTDepositPool.getRsETHAmountToMint` and `LRTOracle._getTotalEthInProtocol` on L1 for consistency.

---

### Proof of Concept

1. Admin calls `addSupportedToken(WBTC_ADDRESS, WBTC_ETH_ORACLE)` on any L2 pool (e.g., `RSETHPoolV3`).
2. Attacker (or any user) calls `deposit(WBTC_ADDRESS, 1e8, "ref")` — depositing 1 WBTC.
3. `viewSwapRsETHAmountAndFee(1e8, WBTC_ADDRESS)` computes:
   - `fee = 1e8 * feeBps / 10_000` (negligible)
   - `tokenToETHRate = 15e18` (from Chainlink WBTC/ETH oracle)
   - `rsETHAmount = ~1e8 * 15e18 / 1.05e18 ≈ 1.43e9`
4. `wrsETH.mint(msg.sender, 1.43e9)` — user receives `1.43e9` units of wrsETH (18-decimal token), i.e., `~1.43e-9` wrsETH.
5. `IERC20(WBTC).safeTransferFrom(msg.sender, address(this), 1e8)` — 1 WBTC (~$60,000+) is taken from the user.
6. The pool bridges the WBTC to L1, increasing protocol TVL by ~15 ETH with no corresponding rsETH minted, diluting the loss across existing holders.

The depositor has lost 1 WBTC and holds wrsETH worth essentially $0.

### Citations

**File:** contracts/pools/RSETHPool.sol (L343-346)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L282-292)
```text
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-36)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
