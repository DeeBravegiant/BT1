Audit Report

## Title
Token Decimal Mismatch in `viewSwapRsETHAmountAndFee` Causes Severe Under-Minting of wrsETH for Non-18 Decimal Tokens - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The two-argument overload `viewSwapRsETHAmountAndFee(uint256 amount, address token)` in all five L2 pool contracts computes the wrsETH mint amount by multiplying the raw token amount directly by the 18-decimal oracle rate, without first normalizing the token amount to 18 decimals. For any ERC-20 token with `d < 18` decimals added via `addSupportedToken`, every depositor receives `10^(18−d)` times less wrsETH than the economically correct amount. The deposited tokens are transferred to the pool and bridged to L1 while the user retains only a dust wrsETH balance.

## Finding Description
The formula in `RSETHPoolV3.sol` lines 324–334 is:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
uint256 rsETHToETHrate = getRate();                                    // 18-dec: ETH per 1 full rsETH
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // 18-dec: ETH per 1 full token
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;        // ← no decimal normalisation
```

`tokenToETHRate` from `ChainlinkOracleForRSETHPoolCollateral.getRate()` is normalized to 18 decimals and represents the ETH value of **one full token unit** (e.g., 1 USDC). When `amount` is expressed in `d`-decimal raw units (e.g., `1e9` for 1000 USDC at 6 decimals), the product `amountAfterFee * tokenToETHRate` is `10^(18−d)` times too small. The same pattern is replicated verbatim in `RSETHPoolV3ExternalBridge.sol` (lines 442–452), `RSETHPoolV3WithNativeChainBridge.sol` (lines 360–370), `RSETHPool.sol` (lines 335–346), and `RSETHPoolNoWrapper.sol` (lines 301–311).

The `addSupportedToken` function imposes no decimal constraint — it only checks that the oracle returns a non-zero rate:

```solidity
if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
```

The deposit function then transfers the full raw token amount and mints the severely under-counted wrsETH:

```solidity
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
wrsETH.mint(msg.sender, rsETHAmount);
```

No existing guard in the deposit or pricing path checks or normalizes token decimals.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

A user depositing a non-18-decimal token receives a wrsETH balance that is `10^(18−d)` times smaller than the economically correct amount. For USDC (`d = 6`) the shortfall factor is `10^12`. The deposited tokens are transferred to the pool and eventually bridged to L1; the user retains only a dust wrsETH balance with negligible redemption value. No third-party theft occurs, but the contract fails to deliver the exchange it promises.

## Likelihood Explanation
**Low.** The vulnerability is latent: it activates only when a TIMELOCK_ROLE holder adds a token with `decimals() < 18` via `addSupportedToken`. All currently supported assets (native ETH, wstETH) have 18 decimals, so no user is harmed today. However, the protocol architecture explicitly supports adding new collateral tokens, and the code contains no guard against non-18-decimal tokens. A future governance decision to add a stablecoin or other low-decimal asset would silently activate the bug for every subsequent depositor of that token. The trigger is a legitimate governance action, not malicious governance capture.

## Recommendation
Normalize the deposited amount to 18 decimals before applying the rate formula in all five pool contracts:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 amountIn18 = amountAfterFee * 1e18 / (10 ** uint256(tokenDecimals));
rsETHAmount = amountIn18 * tokenToETHRate / rsETHToETHrate;
```

Alternatively, enforce an 18-decimal requirement in `addSupportedToken` with `require(IERC20Metadata(token).decimals() == 18)`. Also apply the symmetric fix to `viewSwapAssetToPremintedRsETH` in `RSETHPoolV3.sol` (line 400), which has the inverse over-payment bug for non-18-decimal tokens.

## Proof of Concept
**Setup:** USDC (6 decimals) added with oracle returning `4e14` (≈ 0.0004 ETH/USDC); `rsETHToETHrate = 1.05e18`.

**User deposits 1,000 USDC → `amount = 1e9`:**

```
amountAfterFee ≈ 1e9
rsETHAmount    = 1e9 * 4e14 / 1.05e18
               = 4e23 / 1.05e18
               ≈ 380,952 wei of wrsETH
```

**Correct amount:**
```
1,000 USDC = 0.4 ETH → 0.4 / 1.05 rsETH ≈ 3.81e17 wei of wrsETH
```

The user receives **~381,000 wei** instead of **~3.81 × 10¹⁷ wei** — a shortfall of `10^12×`. The 1,000 USDC is transferred to the pool; the user's wrsETH balance is economically worthless.

**Foundry test plan:**
1. Deploy `RSETHPoolV3` with a mock oracle returning `4e14` for a mock 6-decimal token.
2. Call `addSupportedToken(mockUSDC, mockOracle)` from TIMELOCK_ROLE.
3. Call `deposit(mockUSDC, 1e9, "")` from a user address.
4. Assert `wrsETH.balanceOf(user) < 1e12` (dust) and `mockUSDC.balanceOf(pool) == 1e9` (full transfer).
5. Assert the correct amount would be `≈ 3.81e17` by computing `1e9 * 1e12 * 4e14 / 1.05e18`.