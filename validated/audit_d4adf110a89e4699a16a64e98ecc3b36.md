### Title
Missing Token Decimal Normalization in `viewSwapRsETHAmountAndFee` Causes Massive Undervaluation for Non-18-Decimal Tokens - (`contracts/pools/RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, `RSETHPoolNoWrapper.sol`, `agETH/AGETHPoolV3.sol`)

---

### Summary

All L2 pool contracts compute the rsETH/agETH output for ERC20 token deposits using a formula that implicitly assumes the deposited token has 18 decimals. When a non-18-decimal token (e.g., USDC with 6 decimals, WBTC with 8 decimals) is added as a supported collateral, depositors receive a quantity of rsETH that is `10^(18 - tokenDecimals)` times smaller than the correct amount. The deposited tokens accumulate in the pool and are bridged to L1, permanently stealing the user's funds.

---

### Finding Description

Every pool contract exposes a public `deposit(address token, uint256 amount, string referralId)` function. The rsETH output is computed in `viewSwapRsETHAmountAndFee`:

```solidity
// RSETHPoolV3.sol lines 324-334
function viewSwapRsETHAmountAndFee(uint256 amount, address token) public view ... {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;

    uint256 rsETHToETHrate = getRate();                                    // 1e18-scaled
    uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // 1e18-scaled

    rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;        // ← BUG
}
``` [1](#0-0) 

The oracle wrapper `ChainlinkOracleForRSETHPoolCollateral.getRate()` correctly normalises the Chainlink price to 1e18 precision regardless of the feed's native decimals:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [2](#0-1) 

So `tokenToETHRate` is always in 1e18 precision. However, `amountAfterFee` is in the token's **native** decimal precision. For an 18-decimal token the formula is dimensionally consistent; for a 6-decimal token it is not.

**Correct derivation for ETH deposits (works):**
```
rsETHAmount = amountAfterFee(1e18) * 1e18 / rsETHToETHrate(1e18)
            = (amount_in_ETH) / rsETHToETHrate  →  correct 1e18-scaled rsETH
``` [3](#0-2) 

**Broken derivation for 6-decimal token deposits:**
```
amountAfterFee = 1e6   (1 USDC)
tokenToETHRate = 3.33e14  (1/3000 ETH per USDC, 1e18-scaled)
rsETHToETHrate = 1.05e18

rsETHAmount = 1e6 * 3.33e14 / 1.05e18 = 317 wei of rsETH
```
Correct answer: `1e6 * 3.33e14 * 1e18 / (1.05e18 * 1e6) = 3.17e14 wei of rsETH`

The formula is off by `10^(18 - 6) = 1e12`.

The identical bug is present in every pool variant:

- `RSETHPoolV3.sol` line 334 [4](#0-3) 
- `RSETHPoolV3ExternalBridge.sol` line 452 [5](#0-4) 
- `RSETHPoolV3WithNativeChainBridge.sol` line 370 [6](#0-5) 
- `RSETHPoolNoWrapper.sol` line 311 [7](#0-6) 
- `AGETHPoolV3.sol` line 194 [8](#0-7) 

The `addSupportedToken` function in each pool does not check or restrict token decimals: [9](#0-8) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A user depositing 1,000 USDC (1,000e6 units) receives only ~317,000 wei of rsETH instead of ~3.17e17 wei. The USDC is transferred into the pool (`safeTransferFrom`) and later bridged to L1 by the bridger. The user permanently loses ~$1,000 worth of USDC in exchange for a negligible dust amount of rsETH. The error magnitude is `10^12` for USDC and `10^10` for WBTC.

---

### Likelihood Explanation

**Medium.** The `addSupportedToken` function is gated behind `TIMELOCK_ROLE` / `DEFAULT_ADMIN_ROLE`. However, the protocol is explicitly designed to expand collateral support on L2 chains (the version history in `RSETHPoolV3ExternalBridge` already shows wstETH being added in reinitializer(5)). USDC and WBTC are the most natural next candidates on any L2. There is no documentation, comment, or on-chain guard warning against non-18-decimal tokens. An admin acting in good faith would trigger the bug.

---

### Recommendation

Normalise `amountAfterFee` to 18 decimals before applying the rate formula:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

rsETHAmount = amountAfterFee
    * 10 ** (18 - IERC20Metadata(token).decimals())  // decimal normalisation
    * tokenToETHRate
    / rsETHToETHrate;
```

Alternatively, store each token's decimal factor at `addSupportedToken` time to avoid the external call on every swap.

---

### Proof of Concept

1. Admin calls `addSupportedToken(USDC, chainlinkUSDCOracle, bridge)` on `RSETHPoolV3`.
2. Attacker (or any user) calls `deposit(USDC, 1_000e6, "ref")`.
3. Pool pulls 1,000 USDC from the user.
4. `viewSwapRsETHAmountAndFee(1_000e6, USDC)` computes:
   - `tokenToETHRate = 3.33e14` (USDC/ETH at $3,000/ETH, 1e18-scaled)
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1_000e6 * 3.33e14 / 1.05e18 = 317_142` (317,142 wei ≈ 3.17e-13 rsETH)
5. Pool mints 317,142 wei of wrsETH to the user — worth essentially $0.
6. The 1,000 USDC sits in the pool and is later bridged to L1 by the bridger, permanently lost to the user.

Correct rsETH output should be `~3.17e17` wei (~0.317 rsETH, worth ~$1,000).

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L324-334)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-555)
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
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-34)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L452-452)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L370-370)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L311-311)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L194-194)
```text
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```
