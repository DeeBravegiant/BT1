Audit Report

## Title
Missing Zero-Output Guard Allows Silent Loss of Dust Token Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
All five L2 pool contracts (`RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) compute rsETH output via integer division without guarding against a zero result. When a deposited amount is small enough that `amountAfterFee * tokenToETHRate < rsETHToETHrate`, the division truncates to zero, the user's tokens are transferred into the pool, and 0 rsETH is returned. The contract silently succeeds with no refund mechanism.

## Finding Description
Every token deposit path follows the same pattern across all five pool variants:

1. `if (amount == 0) revert InvalidAmount()` — only guards against a literal zero input.
2. `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` — tokens are taken unconditionally.
3. `(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token)` — computes output via integer division.
4. `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` — truncates to 0 when `amountAfterFee * tokenToETHRate < rsETHToETHrate`.
5. `safeTransfer(msg.sender, 0)` or `wrsETH.mint(msg.sender, 0)` — succeeds silently, returning nothing.

The root cause is confirmed in the code:

- `RSETHPool.sol` L346: `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;` [1](#0-0) 
- `RSETHPool.sol` L294–302: only `amount == 0` is checked; no post-computation zero guard exists before the transfer. [2](#0-1) 
- `RSETHPoolNoWrapper.sol` L311: same truncating division. [3](#0-2) 
- `RSETHPoolV3.sol` L334: same truncating division. [4](#0-3) 
- `RSETHPoolV3ExternalBridge.sol` L401–409: tokens transferred before output computed, no zero-output revert. [5](#0-4) 
- `RSETHPoolV3WithNativeChainBridge.sol` L318–326: same pattern. [6](#0-5) 

By contrast, the L1 `LRTDepositPool.depositAsset` exposes a `minRSETHAmountExpected` parameter and enforces it in `_beforeDeposit`, preventing this class of issue entirely. [7](#0-6) 

## Impact Explanation
A depositor who sends a dust amount of a supported ERC-20 token receives 0 rsETH in return. The tokens are not refunded; they accumulate in the pool balance and are eventually bridged to L1 as protocol revenue. The user suffers a complete loss of the deposited dust amount. This matches the allowed impact: **Low — contract fails to deliver promised returns, but doesn't lose value** (from the protocol's perspective). The maximum loss per call is bounded by `⌈rsETHToETHrate / tokenToETHRate⌉ − 1` wei of the deposited token, which is sub-cent for all realistic LST oracle values.

## Likelihood Explanation
Any unprivileged external caller can trigger this by calling `deposit(token, dustAmount, "")` with no special role, no front-running, and no second error required. The only precondition is that the deposited amount falls below the truncation threshold. This can occur accidentally via UI rounding errors, residual amounts in contract integrations, or test transactions. Likelihood is low-to-medium; normal users deposit meaningful amounts, but the path is fully reachable with zero barriers.

## Recommendation
Add a zero-output guard immediately after computing `rsETHAmount` in every `deposit` function across all five pool contracts:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
if (rsETHAmount == 0) revert InvalidAmount(); // or a dedicated ZeroRsETHOutput() error
```

Alternatively, add a `minRsETHAmountExpected` parameter to each `deposit` function (mirroring `LRTDepositPool.depositAsset`) so callers can enforce their own slippage floor.

## Proof of Concept
Assume `rsETHToETHrate = 1.05e18`, `tokenToETHRate = 1e18`, `feeBps = 0`. Call on any pool variant:

```
deposit(wstETH, 1, "")
```

Execution trace:
1. `amount = 1` passes `if (amount == 0)` check.
2. `safeTransferFrom(msg.sender, pool, 1)` — 1 wei of wstETH leaves the user.
3. `fee = 0`; `amountAfterFee = 1`.
4. `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation).
5. `safeTransfer(msg.sender, 0)` or `mint(msg.sender, 0)` — user receives nothing.
6. Transaction succeeds; user has permanently lost 1 wei of wstETH.

A Foundry fuzz test targeting `deposit(token, amount, "")` with `amount` bounded to `[1, rsETHToETHrate/tokenToETHRate - 1]` would reliably reproduce this for all five pool contracts.

### Citations

**File:** contracts/pools/RSETHPool.sol (L294-302)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPool.sol (L346-346)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L311-311)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L401-409)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L318-326)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/LRTDepositPool.sol (L99-111)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
```
