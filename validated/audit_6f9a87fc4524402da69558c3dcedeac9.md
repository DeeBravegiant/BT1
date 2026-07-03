Audit Report

## Title
Missing Zero-Output Guard in L2 Pool Deposit Functions Allows Dust Deposits to Be Silently Consumed - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
All L2 pool `deposit()` functions compute `rsETHAmount` via integer division that can truncate to zero for dust inputs, yet no guard prevents `wrsETH.mint(msg.sender, 0)` from executing. A depositor sending a sufficiently small amount of ETH or tokens will have their funds accepted by the pool, receive zero wrsETH in return, and hold no claim on the deposited value.

## Finding Description
In `RSETHPoolV3.viewSwapRsETHAmountAndFee()`, the output is computed as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

When `amountAfterFee * 1e18 < rsETHToETHrate` (e.g., `rsETHToETHrate = 1.05e18` and `amount = 1 wei`), Solidity integer division yields `rsETHAmount = 0`. The deposit function then unconditionally proceeds:

```solidity
if (amount == 0) revert InvalidAmount();   // only guards input, not output
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);      // mints 0 — no guard
```

The only existing guard (`amount == 0`) checks the raw input, not the computed output. The `limitDailyMint` modifier also silently passes when `rsETHAmount == 0`, adding 0 to `dailyMintAmount` without reverting. The same pattern is confirmed in `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolNoWrapper` (which calls `rsETH.safeTransfer(msg.sender, 0)` instead of mint). The `wrsETH.mint` implementation has no zero-amount guard either — it directly calls `_mint(_to, _amount)`. By contrast, `LRTDepositPool.depositETH()` on L1 enforces `rsethAmountToMint >= minRSETHAmountExpected`, a protection absent from all L2 pools.

## Impact Explanation
**Low — Contract fails to deliver promised returns.** A user depositing a dust amount (any value where `amountAfterFee * 1e18 < rsETHToETHrate`) will have their ETH or tokens permanently retained in the pool with no corresponding wrsETH liability issued to them. The deposited ETH is eventually bridged to L1 as protocol liquidity, leaving the user with no recourse. The transaction succeeds silently, emitting a `SwapOccurred` event with `rsETHAmount = 0`.

## Likelihood Explanation
**Low.** rsETH appreciates above 1 ETH over time as yield accrues, so any deposit below `rsETHToETHrate / 1e18` wei after fees triggers this. No privileged access, oracle manipulation, or external conditions are required. The condition arises from ordinary integer division on dust inputs by any unprivileged caller. Users are not warned and the transaction does not revert.

## Recommendation
Add a zero-output guard immediately after computing `rsETHAmount` in all L2 pool deposit functions:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, introduce a `minRSETHAmountExpected` parameter mirroring `LRTDepositPool.depositETH()`, allowing callers to enforce their own slippage tolerance. The fix must be applied to `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolNoWrapper`.

## Proof of Concept
1. Deploy `RSETHPoolV3` with `rsETHToETHrate = 1.05e18` (realistic post-yield-accrual rate) and `feeBps = 0`.
2. Call `RSETHPoolV3.deposit{value: 1}("")` (1 wei ETH).
3. `viewSwapRsETHAmountAndFee(1)` returns: `fee = 0`, `amountAfterFee = 1`, `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `limitDailyMint` adds 0 to `dailyMintAmount` — no revert.
5. `wrsETH.mint(msg.sender, 0)` executes — user receives 0 wrsETH.
6. Pool retains 1 wei ETH; `SwapOccurred` event emits with `rsETHAmount = 0`.
7. Repeat for `RSETHPoolNoWrapper`: `rsETH.safeTransfer(msg.sender, 0)` succeeds silently.

Foundry fuzz test plan: fuzz `amount` in `[1, rsETHToETHrate / 1e18 - 1]` range, assert `wrsETH.balanceOf(depositor) > 0` after each deposit — this invariant will fail for all inputs in that range. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-124)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3.sol (L256-262)
```text
        if (amount == 0) revert InvalidAmount();

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L375-383)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L292-299)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L235-242)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

```

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
