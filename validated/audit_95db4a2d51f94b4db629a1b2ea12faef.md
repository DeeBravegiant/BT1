Audit Report

## Title
Rounding to Zero in Pool Token Deposit Calculation Causes Depositor to Lose Tokens Without Receiving wrsETH - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `deposit` function in `RSETHPoolV3` (and sibling pool contracts) transfers the depositor's tokens into the pool before computing the wrsETH mint amount. When the deposit amount is small enough that `amountAfterFee * tokenToETHRate / rsETHToETHrate` truncates to zero via Solidity integer division, the user's tokens are permanently consumed by the pool while `wrsETH.mint(msg.sender, 0)` is called, leaving the depositor with nothing. No guard exists on the computed zero output.

## Finding Description
In `RSETHPoolV3.sol`, `deposit` executes in this order:

1. `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` — tokens leave the user (L284)
2. `(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token)` — computes output (L286)
3. `wrsETH.mint(msg.sender, rsETHAmount)` — mints the (possibly zero) result (L290)

`viewSwapRsETHAmountAndFee` computes:
```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, Solidity integer division yields `rsETHAmount = 0`.

The only input guard is `if (amount == 0) revert InvalidAmount()` (L282), which does not prevent a non-zero `amount` from producing a zero output. The `limitDailyMint` modifier (L96–125) also calls `viewSwapRsETHAmountAndFee` and accumulates `rsETHAmount` into `dailyMintAmount`; when `rsETHAmount = 0`, the check `0 + 0 > dailyMintLimit` trivially passes, so the modifier provides no protection. The `InterimRSETHOracle` enforces `rate >= 1e18`, and the pool's `setSupportedTokenOracle` only checks `getRate() != 0` with no lower bound, so `tokenToETHRate` can be arbitrarily small relative to `rsETHToETHrate`. In the worst-case ratio (`tokenToETHRate = 1e16`, `rsETHToETHrate = 1e18`), any deposit where `amountAfterFee < 100` yields `rsETHAmount = 0`. The identical pattern exists in `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, and `RSETHPoolV2NBA`.

## Impact Explanation
A depositor who sends a small token amount has their tokens permanently transferred into the pool contract while receiving zero wrsETH. The tokens are not recoverable by the user; they silently inflate the pool's token balance. This matches **Low — Contract fails to deliver promised returns**: the protocol accepts the deposit but fails to issue the corresponding wrsETH, permanently denying the depositor their entitled position.

## Likelihood Explanation
Any external caller can invoke `deposit(address token, uint256 amount, string referralId)` with a non-zero but small `amount`. No role or whitelist is required. The condition is triggered whenever `amountAfterFee * tokenToETHRate < rsETHToETHrate`, which is achievable with small amounts of any supported low-rate token. The likelihood is low in practice because the affected amounts are tiny (sub-100 wei in the worst case), but the path is fully permissionless and requires no special conditions beyond a small deposit.

## Recommendation
Add a zero-amount guard on the computed `rsETHAmount` before transferring tokens or immediately after computing the swap output:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, move the token transfer after the output computation so that a zero-output revert does not strand user funds. A minimum deposit amount per token (similar to `LRTDepositPool`'s `minAmountToDeposit`) would also prevent this class of truncation.

## Proof of Concept
Assume:
- `feeBps = 0`
- `tokenToETHRate = 1e16` (minimum non-zero value accepted by pool oracle setter)
- `rsETHToETHrate = 1e18` (rsETH at 1 ETH, minimum enforced by `InterimRSETHOracle`)
- User calls `deposit(token, 50, "")`

Execution trace:
1. `fee = 50 * 0 / 10_000 = 0`
2. `amountAfterFee = 50`
3. `rsETHAmount = 50 * 1e16 / 1e18 = 5e17 / 1e18 = 0` (integer truncation)
4. `IERC20(token).safeTransferFrom(msg.sender, address(this), 50)` — 50 wei transferred out of user
5. `wrsETH.mint(msg.sender, 0)` — user receives nothing

Foundry fuzz test plan: fuzz `amount` in `[1, 99]` with `tokenToETHRate = 1e16` and `rsETHToETHrate = 1e18`; assert that after `deposit`, `wrsETH.balanceOf(user) > 0` — this assertion will fail for all inputs in the fuzzed range, confirming the bug. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-124)
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
```

**File:** contracts/pools/RSETHPoolV3.sol (L282-290)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
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

**File:** contracts/pools/RSETHPoolV3.sol (L583-586)
```text
        UtilLib.checkNonZeroAddress(oracle);
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```

**File:** contracts/pools/RSETHPool.sol (L294-304)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/pools/RSETHPool.sol (L335-347)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L41-43)
```text
    function _setRate(uint256 newRate) internal {
        if (newRate < 1e18) revert InvalidRate();
        rate = newRate;
```
