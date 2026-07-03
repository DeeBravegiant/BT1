### Title
Token Decimal Mismatch in `viewSwapRsETHAmountAndFee` Causes Depositor Fund Loss for Non-18-Decimal Collateral - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

The L2 pool contracts compute the amount of `wrsETH`/`rsETH` to mint using the formula `amountAfterFee * tokenToETHRate / rsETHToETHrate`. Both rate values are normalized to 1e18, but `amountAfterFee` is in the token's native smallest unit. For 18-decimal tokens this cancels correctly; for any token with fewer decimals (e.g., USDC at 6, WBTC at 8) the result is off by `10^(18 - tokenDecimals)`, causing depositors to receive that many orders of magnitude fewer `wrsETH` than the fair value of their deposit. The `addSupportedToken` function enforces no decimal constraint, so the broken path is reachable whenever governance adds a non-18-decimal collateral.

---

### Finding Description

Every L2 pool variant (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, `RSETHPoolNoWrapper`, `AGETHPoolV3`) shares the same token-deposit pricing path:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 324-334
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;

uint256 rsETHToETHrate = getRate();                                    // 1e18-scaled
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // 1e18-scaled

rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` is the price of **one whole token** expressed in 1e18 (e.g., `ChainlinkOracleForRSETHPoolCollateral.getRate()` explicitly normalises to 1e18 regardless of the feed's native decimals). `amountAfterFee`, however, is in the token's **smallest unit**. For an 18-decimal token the implicit `1e18` in the numerator and denominator cancel; for a token with `d < 18` decimals the numerator is short by `10^(18-d)`, so the minted amount is `10^(18-d)` times too small.

`addSupportedToken` performs no decimal check:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 541-554
function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
    ...
    if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    ...
}
```

The same structural flaw exists in `LRTDepositPool.getRsETHAmountToMint`:

```solidity
// contracts/LRTDepositPool.sol  line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

---

### Impact Explanation

If a non-18-decimal token (e.g., USDC at 6 decimals, WBTC at 8 decimals) is added as supported collateral:

**USDC example (6 decimals, 1 USDC ≈ 0.0005 ETH, rsETH rate ≈ 1.05 ETH):**
- User deposits 1 USDC → `amountAfterFee = 1e6`
- `tokenToETHRate = 5e14` (0.0005 × 1e18)
- `rsETHToETHrate = 1.05e18`
- Computed: `1e6 × 5e14 / 1.05e18 ≈ 476` rsETH units
- Correct: `~4.76e14` rsETH units (0.000476 rsETH)
- **Error factor: 10^12 — user receives one trillion times fewer tokens than owed**

The deposited USDC is transferred into the pool and is not refundable. The user permanently loses their collateral in exchange for a dust amount of `wrsETH`. This constitutes direct theft of depositor funds at the protocol level.

**Impact: Critical — direct permanent loss of depositor funds.**

---

### Likelihood Explanation

The vulnerability is latent: it is inert while only 18-decimal LSTs (stETH, wstETH, cbETH, rETH) are supported, which is the current deployment state. It activates the moment governance adds any token with fewer than 18 decimals. The protocol is explicitly designed to be generic (the `addSupportedToken` interface accepts any ERC20), and stablecoins or wrapped BTC are natural future collateral candidates. No attacker action is required beyond depositing after such a token is listed.

**Likelihood: Low** (requires a governance listing of a non-18-decimal token, which is a plausible protocol expansion step).

---

### Recommendation

Normalise `amountAfterFee` to 18 decimals before applying the rate, or scale the result back after:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 amountNormalized = amountAfterFee * 10 ** (18 - tokenDecimals);
rsETHAmount = amountNormalized * tokenToETHRate / rsETHToETHrate;
```

Alternatively, enforce in `addSupportedToken` that only 18-decimal tokens may be listed:

```solidity
require(IERC20Metadata(token).decimals() == 18, "Only 18-decimal tokens supported");
```

Apply the same fix to `LRTDepositPool.getRsETHAmountToMint` and `AGETHPoolV3.viewSwapAgETHAmountAndFee`.

---

### Proof of Concept

**Entry path (unprivileged depositor):**

1. Governance calls `RSETHPoolV3.addSupportedToken(USDC, usdcOracle)` — legitimate admin action, no compromise required.
2. Attacker (or any user) calls `RSETHPoolV3.deposit(USDC, 1_000_000e6, "")` (1,000,000 USDC ≈ $1,000,000).
3. `viewSwapRsETHAmountAndFee(1_000_000e6, USDC)` executes:
   - `amountAfterFee = 1_000_000e6` (6-decimal units)
   - `tokenToETHRate = 5e14` (0.0005 ETH/USDC × 1e18)
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1_000_000e6 × 5e14 / 1.05e18 ≈ 4.76e8` (≈ 0.000000476 wrsETH)
4. User receives ~0 wrsETH; 1,000,000 USDC is locked in the pool permanently.

**Correct result** would be `~4.76e20` wrsETH units (≈ 476 wrsETH worth ~$1,000,000).

The root cause line is: [1](#0-0) 

The same defect appears identically in: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

The permissionless entry point is: [8](#0-7) 

The unconstrained token registration that enables the path: [9](#0-8)

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

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L311-311)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L194-194)
```text
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
