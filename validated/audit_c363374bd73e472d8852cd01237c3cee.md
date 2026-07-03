Audit Report

## Title
Token Decimal Mismatch in `viewSwapRsETHAmountAndFee` Causes Incorrect rsETH Amount for Non-18-Decimal Tokens - (File: contracts/pools/RSETHPoolNoWrapper.sol)

## Summary
`viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes rsETH output by multiplying the raw token amount directly by the 1e18-normalized `tokenToETHRate`, without first normalizing the token amount to 18 decimals. For tokens with fewer than 18 decimals (e.g., USDC=6, WBTC=8), users receive a negligible rsETH amount while their full token balance is transferred to the pool. `addSupportedToken` imposes no decimal restriction, making any non-18-decimal collateral token a trigger.

## Finding Description
The ETH deposit path at line 285 correctly scales:
```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
Here `amountAfterFee` is in wei (18 decimals), so the `1e18` factor cancels the rate's denominator correctly.

The token deposit path at line 311 omits this normalization:
```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
`tokenToETHRate` from `ChainlinkOracleForRSETHPoolCollateral.getRate()` (line 34) is the price of **one whole token** in ETH, normalized to 1e18. For USDC/ETH it is ~3.33e14. But `amountAfterFee` is in raw USDC units (1e6 per USDC), not in 18-decimal units. The formula therefore implicitly treats 1 USDC as if it were `1e-12` USDC.

Correct formula:
```
rsETHAmount = amountAfterFee * 1e18 / 10^tokenDecimals * tokenToETHRate / rsETHToETHrate
```

For 18-decimal tokens the current formula is accidentally correct (`1e18 / 1e18 = 1`). For any other token it is wrong by a factor of `10^(18 - tokenDecimals)`.

`addSupportedToken` (lines 573–592) checks only that the oracle returns a non-zero rate; there is no `decimals() == 18` guard. Any TIMELOCK-approved non-18-decimal token immediately exposes every depositor.

Exploit flow:
1. Admin adds USDC (6 decimals) via `addSupportedToken`.
2. User approves pool for 1000 USDC (`1000e6` raw units) and calls `deposit(USDC, 1000e6, "ref")`.
3. Line 262: `safeTransferFrom` moves 1000 USDC from user to pool.
4. Line 264: `viewSwapRsETHAmountAndFee(1000e6, USDC)` computes `rsETHAmount = 1000e6 * 3.33e14 / 1e18 = 333000` (≈ 3.33e-13 rsETH).
5. Line 268: `rsETH.safeTransfer(msg.sender, 333000)` — user receives ~0 rsETH.
6. User has permanently lost 1000 USDC; the pool retains both the USDC and essentially all of its rsETH reserve.

No existing check prevents this. The `onlySupportedToken` modifier only verifies the token has a registered oracle; it does not validate decimals.

## Impact Explanation
**Critical — Direct theft of user funds.** A depositor's full token balance is taken by the pool via `safeTransferFrom` but the rsETH returned is scaled down by `10^(18 - tokenDecimals)`. For USDC this is a 1e12× loss per deposit. The depositor has no recovery path: the pool holds the USDC and the rsETH reserve is nearly untouched. This matches the allowed impact "Direct theft of any user funds, whether at-rest or in-motion."

## Likelihood Explanation
Medium-to-high. The `addSupportedToken` function accepts any ERC20 with a valid oracle and no decimal restriction. USDC, USDT, and WBTC are standard L2 collateral candidates. Any user who calls the public `deposit(address, uint256, string)` entry point with such a token immediately suffers the full loss. No attacker sophistication is required; the loss is automatic and repeatable for every deposit.

## Recommendation
Normalize `amountAfterFee` to 18 decimals before applying the rate:
```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 10 ** (18 - tokenDecimals);
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```
Alternatively, enforce at registration time:
```solidity
require(IERC20Metadata(token).decimals() == 18, "Only 18-decimal tokens supported");
```

## Proof of Concept
Foundry fork test outline:
1. Deploy or fork a chain where `RSETHPoolNoWrapper` is live.
2. Call `addSupportedToken(USDC, usdcChainlinkOracle, usdcBridge)` from the TIMELOCK account.
3. Record user's USDC balance and pool's rsETH balance before deposit.
4. User calls `deposit(USDC, 1000e6, "ref")`.
5. Assert `rsETH.balanceOf(user) < 1e6` (received ~333000 wei of rsETH, not ~3.33e14).
6. Assert `IERC20(USDC).balanceOf(pool) == 1000e6` (full USDC retained by pool).
7. Compute expected rsETH = `1000e6 * 1e12 * tokenToETHRate / rsETHToETHrate` and assert actual is `1e12×` smaller. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L573-592)
```text
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);
        UtilLib.checkNonZeroAddress(bridge);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (tokenBridge[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;

        emit AddSupportedToken(token, oracle, bridge);
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
