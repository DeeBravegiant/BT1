### Title
No check that `rsETHAmount` is non-zero in L2 pool `deposit()` functions allows user funds to be permanently lost due to rounding - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
All L2 pool deposit functions (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, `RSETHPoolNoWrapper`) compute the amount of wrsETH to mint via integer division. When the deposit amount is small enough that the division truncates to zero, the user's ETH or tokens are accepted and permanently retained by the pool while zero wrsETH is minted. There is no guard analogous to the `if (rsETHAmount == 0) revert` pattern recommended in the reference report.

### Finding Description
In every L2 pool variant, the ETH deposit path is:

```solidity
// RSETHPoolV3.sol deposit()
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0 if rsETHAmount == 0
```

`viewSwapRsETHAmountAndFee` computes:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // integer division
```

When `amountAfterFee * 1e18 < rsETHToETHrate` (e.g., `amountAfterFee = 1 wei` and `rsETHToETHrate ≈ 1.05e18`), `rsETHAmount` truncates to `0`. The only guard present is `if (amount == 0) revert InvalidAmount()`, which does not catch the case where a non-zero `amount` still produces a zero output. The user's ETH is accepted into the pool balance (and eventually bridged to L1) with no wrsETH issued in return. Unlike `LRTDepositPool`, which exposes a `minRSETHAmountExpected` slippage parameter, the L2 pool `deposit()` functions provide no such protection.

The same truncation applies to the ERC-20 token deposit path:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

where a token whose `tokenToETHRate < rsETHToETHrate` can produce zero for small `amountAfterFee`.

### Impact Explanation
A depositor who sends a dust amount of ETH (e.g., 1 wei) receives 0 wrsETH while their ETH is permanently absorbed into the pool. The funds are not recoverable by the user. This matches the "contract fails to deliver promised returns" category (Low), because the value lost per transaction is bounded by one unit of the deposited asset (≤ 1 wei of ETH under normal rate conditions), making the per-call loss negligible. However, the missing check is structurally identical to the reference vulnerability and leaves the door open if rates or fee parameters shift.

### Likelihood Explanation
Under current deployment parameters (`rsETHToETHrate ≈ 1.05e18`, `feeBps` in the low single digits), only a deposit of exactly 1 wei triggers the zero-output path. Any user or integration that passes a dust amount without a minimum-output check will silently lose it. The likelihood is low but non-zero, and no on-chain protection prevents it.

### Recommendation
Add an explicit zero-output guard immediately after computing `rsETHAmount` in every `deposit()` function across all L2 pool variants:

```solidity
if (rsETHAmount == 0) revert ZeroOutputAmount();
```

This mirrors the fix applied in the reference report and the pattern already used in `LRTDepositPool` via `minRSETHAmountExpected`.

### Proof of Concept
1. Deploy `RSETHPoolV3` with `feeBps = 0` and `rsETHToETHrate = 1.05e18`.
2. Call `deposit{value: 1}("")` (1 wei ETH).
3. `viewSwapRsETHAmountAndFee(1)` returns `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `wrsETH.mint(msg.sender, 0)` executes without revert.
5. Caller's 1 wei is in the pool; caller holds 0 wrsETH.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L294-300)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L237-243)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
