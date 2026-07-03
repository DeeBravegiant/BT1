Audit Report

## Title
Token Decimal Miscalculation in `viewSwapRsETHAmountAndFee` Leads to Near-Zero wrsETH Minting for Non-18 Decimal Tokens - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

## Summary
`RSETHPoolV3` and `RSETHPoolV3WithNativeChainBridge` both compute the wrsETH mint amount for non-ETH ERC-20 deposits using `amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalizing `amountAfterFee` to 18-decimal precision. For any token with fewer than 18 decimals (e.g., USDC at 6, WBTC at 8), the result is `10^(18 − tokenDecimals)` times too small, causing the depositor to receive near-zero wrsETH while their full token balance is permanently locked in the pool.

## Finding Description
The oracle wrapper `ChainlinkOracleForRSETHPoolCollateral.getRate()` normalizes the Chainlink price to 1e18 precision and returns the price of **one full token** in ETH:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L34
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

For USDC this yields `tokenToETHRate ≈ 4e14` (price of 1 full USDC in ETH, 1e18-scaled).

The ETH deposit path correctly accounts for the 1e18 unit of `msg.value`:

```solidity
// RSETHPoolV3.sol L307
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

The non-ETH token path omits the equivalent normalization:

```solidity
// RSETHPoolV3.sol L334
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

When `amountAfterFee` is in the token's native units (e.g., `1000e6` for 1,000 USDC), the formula computes:

```
1000e6 * 4e14 / 1.05e18 ≈ 380,952 wei  (~3.8e-13 wrsETH)
```

The correct result requires normalizing to 18 decimals first:

```
(1000e6 * 1e12) * 4e14 / 1.05e18 ≈ 3.81e17 wei  (~0.381 wrsETH)
```

The shortfall factor is exactly `10^(18 − 6) = 10^12`. The identical bug exists at `RSETHPoolV3WithNativeChainBridge.sol L370`.

The `limitDailyMint` modifier in both contracts calls the same buggy function to track the daily cap, so the cap is also effectively bypassed for non-18 decimal tokens — the computed rsETH amount is negligible and never triggers `DailyMintLimitExceeded`.

There is no user-accessible recovery path: `swapAssetToPremintedRsETH` is gated to `OPERATOR_ROLE` and `moveAssetsForBridging` to `BRIDGER_ROLE`. Deposited tokens are permanently inaccessible to the depositor.

## Impact Explanation
**Critical — Permanent freezing of user funds.** A user who calls `deposit(token, amount, referralId)` with a non-18 decimal token transfers their full token balance to the pool and receives a near-zero amount of wrsETH. Because no user-callable withdrawal or redemption function exists in either pool contract, the deposited tokens are permanently inaccessible to the user.

## Likelihood Explanation
`addSupportedToken` in both contracts accepts any ERC-20 address and oracle pair with no decimal restriction. Both pools are deployed on L2 chains where non-18 decimal tokens (USDC = 6 decimals, WBTC = 8 decimals) are standard bridged assets and natural candidates for inclusion. Once such a token is listed, any unprivileged depositor who calls `deposit` triggers the loss immediately with no further preconditions. The listing itself is a routine protocol operation, not an attack.

## Recommendation
Normalize `amountAfterFee` to 18-decimal precision before applying the rate formula, mirroring the ETH path:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 10 ** (18 - tokenDecimals);
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Apply the symmetric inverse fix to `viewSwapAssetToPremintedRsETH` (which currently over-returns token amounts for non-18 decimal tokens) and apply both fixes identically to `RSETHPoolV3WithNativeChainBridge`.

## Proof of Concept
**Setup:** USDC (6 decimals) is added as a supported token with `ChainlinkOracleForRSETHPoolCollateral` returning `4e14` (≈ 0.0004 ETH per USDC). rsETH oracle returns `1.05e18`.

**Call:**
```solidity
pool.deposit(USDC, 1_000e6, "ref"); // deposits 1,000 USDC
```

**Execution trace:**
```
amountAfterFee = 1_000e6  (assuming feeBps = 0)
tokenToETHRate = 4e14
rsETHToETHrate = 1.05e18

rsETHAmount = 1_000e6 * 4e14 / 1.05e18
            = 4e23 / 1.05e18
            ≈ 380,952 wei  (~3.8e-13 wrsETH)
```

**Expected:**
```
normalizedAmount = 1_000e6 * 1e12 = 1_000e18
rsETHAmount = 1_000e18 * 4e14 / 1.05e18 ≈ 3.81e17 wei  (~0.381 wrsETH)
```

**Foundry test plan:**
1. Deploy `RSETHPoolV3` with a mock rsETH oracle returning `1.05e18`.
2. Deploy a mock USDC (6 decimals) and a mock `ChainlinkOracleForRSETHPoolCollateral` returning `4e14`.
3. Call `addSupportedToken(USDC, mockOracle)`.
4. Mint 1,000 USDC to a test user; approve the pool.
5. Call `pool.deposit(USDC, 1_000e6, "ref")` from the user.
6. Assert `wrsETH.balanceOf(user) < 1e6` (near-zero) and `IERC20(USDC).balanceOf(pool) == 1_000e6` (tokens locked).
7. Confirm no user-callable function can recover the USDC. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

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

**File:** contracts/pools/RSETHPoolV3.sol (L307-307)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L370-370)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-36)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```
