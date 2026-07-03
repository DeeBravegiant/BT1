Audit Report

## Title
Precision Loss in Token-to-rsETH Rate Calculation Causes Depositors to Receive Zero wrsETH - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `deposit(address token, uint256 amount, string memory referralId)` function transfers user tokens before computing the output amount, and performs no zero-check on the resulting `rsETHAmount`. When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, Solidity integer division truncates to zero, `wrsETH.mint(msg.sender, 0)` executes silently, and the deposited tokens are permanently absorbed into the pool with no wrsETH issued to the user. The same pattern is confirmed across all five pool variants.

## Finding Description
In `RSETHPoolV3.deposit` [1](#0-0) , the only input guard is `if (amount == 0) revert InvalidAmount()` — there is no guard on the computed output. Tokens are pulled via `safeTransferFrom` before `viewSwapRsETHAmountAndFee` is called. Inside that function [2](#0-1) , the final line `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` performs integer division with no zero-result guard. When the numerator is smaller than the denominator, the result is zero. The `limitDailyMint` modifier [3](#0-2)  does not block this: it computes the same `rsETHAmount`, adds it to `dailyMintAmount`, and since `dailyMintAmount + 0 > dailyMintLimit` is false for any positive limit, the modifier passes. Control returns to `deposit`, which calls `wrsETH.mint(msg.sender, 0)` [4](#0-3)  — a no-op mint — while the user's tokens remain in the pool permanently. The identical pattern is present in `RSETHPoolV3ExternalBridge.sol` [5](#0-4) , `RSETHPoolV3WithNativeChainBridge.sol` [6](#0-5) , `AGETHPoolV3.sol` [7](#0-6) , and `RSETHPool.sol` [8](#0-7) .

## Impact Explanation
A depositor who sends a dust amount of a supported token — small enough that `amountAfterFee * tokenToETHRate < rsETHToETHrate` — permanently loses those tokens to the pool with zero wrsETH received. The tokens are not recoverable by the user; they become part of the pool's bridgeable balance. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value** from the allowed impact scope (the pool's aggregate value is not reduced, but the individual depositor receives nothing for their deposit). [9](#0-8) 

## Likelihood Explanation
For standard 18-decimal LSTs (stETH, wstETH, ETHx) with oracle rates near `1e18`, truncation to zero occurs only for sub-unit deposits (e.g., exactly 1 wei of stETH when `rsETHToETHrate ≈ 1.05e18` yields `1 * 1e18 / 1.05e18 = 0`). No minimum deposit floor exists in any of the pool contracts — unlike `LRTDepositPool` which enforces `minAmountToDeposit`. The code path is fully reachable by any unprivileged caller with no special preconditions, but the economic incentive to trigger it is negligible, making accidental triggering (e.g., a contract rounding down to 1 wei) the realistic scenario. Likelihood is low. [10](#0-9) 

## Recommendation
Add an explicit zero-check on `rsETHAmount` (and `agETHAmount`) immediately after the division and revert with a descriptive error:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
if (rsETHAmount == 0) revert InvalidAmount();
```

Apply this fix in all five affected pool contracts. Alternatively, enforce a per-token minimum deposit amount analogous to `LRTDepositPool.minAmountToDeposit`. [11](#0-10) 

## Proof of Concept
Assume `rsETHToETHrate = 1.05e18` and `tokenToETHRate = 1e18` (stETH):

1. Call `RSETHPoolV3.deposit(stETH, 1, "")` — `amount = 1 wei`.
2. `amount == 0` check passes.
3. `limitDailyMint` modifier: `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`; `dailyMintAmount += 0`; modifier passes.
4. `safeTransferFrom(msg.sender, address(this), 1)` — 1 wei of stETH leaves the user permanently.
5. `viewSwapRsETHAmountAndFee(1, stETH)` returns `(0, 0)`.
6. `wrsETH.mint(msg.sender, 0)` — user receives nothing.
7. The 1 wei of stETH is now part of the pool balance, bridgeable to L1, unrecoverable by the user.

Foundry fuzz test sketch:
```solidity
function testFuzz_zeroMintOnDustDeposit(uint256 amount) public {
    vm.assume(amount > 0 && amount * tokenToETHRate < rsETHToETHrate);
    deal(stETH, user, amount);
    vm.prank(user);
    IERC20(stETH).approve(address(pool), amount);
    vm.prank(user);
    pool.deposit(stETH, amount, "");
    assertEq(wrsETH.balanceOf(user), 0);
    assertEq(IERC20(stETH).balanceOf(address(pool)), amount);
}
``` [1](#0-0)

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

**File:** contracts/pools/RSETHPoolV3.sol (L282-292)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-452)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L360-370)
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

**File:** contracts/agETH/AGETHPoolV3.sol (L143-153)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPool.sol (L340-346)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
