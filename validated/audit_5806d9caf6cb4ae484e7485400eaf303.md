### Title
Missing Minimum wrsETH Output Check in L2 Pool Deposit Functions Allows Zero-Mint Loss - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The L2 pool `deposit()` functions across `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolNoWrapper` do not verify that the computed `rsETHAmount` is greater than zero before minting or transferring wrsETH. A depositor sending a dust amount of ETH or tokens that integer-divides to zero wrsETH will lose their deposit while receiving nothing in return.

### Finding Description
In every L2 pool, the wrsETH output is computed as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Due to Solidity integer division, if `amountAfterFee * 1e18 < rsETHToETHrate`, `rsETHAmount` truncates to 0. The function then proceeds unconditionally: [1](#0-0) 

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0 — no guard
```

There is no `require(rsETHAmount > 0)` guard anywhere in the deposit path. The same pattern is present in: [2](#0-1) [3](#0-2) [4](#0-3) 

The `limitDailyMint` modifier also computes `rsETHAmount` and adds it to `dailyMintAmount`; when it is 0, the daily cap is not consumed either, so the transaction passes through silently. [5](#0-4) 

By contrast, the L1 `LRTDepositPool.depositETH()` accepts a `minRSETHAmountExpected` parameter and reverts if the computed mint amount falls below it: [6](#0-5) 

No equivalent protection exists in any L2 pool.

### Impact Explanation
A user depositing a dust amount (e.g., 1 wei ETH when `rsETHToETHrate > 1e18`) will have their ETH accepted by the pool, receive 0 wrsETH, and hold no claim on the deposited ETH. The ETH is eventually bridged to L1 as protocol liquidity. **Impact: Low** — the contract fails to deliver promised returns for dust deposits; the user loses their deposit with no recourse.

### Likelihood Explanation
**Low.** rsETH naturally appreciates above 1 ETH over time as yield accrues, so any deposit of less than 1 wei after fee will trigger this. No oracle manipulation by an unprivileged actor is required or possible; the condition arises from ordinary integer division on dust inputs. Users are not warned and the transaction does not revert.

### Recommendation
Add a zero-output guard in all L2 pool deposit functions:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, introduce a `minRSETHAmountExpected` parameter (mirroring `LRTDepositPool.depositETH()`) so callers can enforce their own slippage tolerance.

### Proof of Concept
1. `rsETHToETHrate = 1.05e18` (rsETH worth 1.05 ETH — realistic after yield accrual).
2. User calls `RSETHPoolV3.deposit{value: 1}("")` (1 wei ETH).
3. `viewSwapRsETHAmountAndFee(1)`:
   - `fee = 1 * feeBps / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation)
4. `wrsETH.mint(msg.sender, 0)` executes — user receives 0 wrsETH.
5. The 1 wei ETH is retained in the pool with no corresponding liability to the user.
6. `limitDailyMint` adds 0 to `dailyMintAmount`, so the daily cap is unaffected and the transaction emits a `SwapOccurred` event with `rsETHAmount = 0`, giving no on-chain signal of the loss. [7](#0-6)

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

**File:** contracts/pools/RSETHPoolV3.sol (L258-263)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L377-383)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L294-299)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L237-242)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
