### Title
Fee Bypass via Integer Division Truncation in Deposit Fee Calculation - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
Multiple L2 pool contracts in the LRT-rsETH protocol charge a basis-point fee on every deposit. The fee is computed with plain Solidity integer division (`amount * feeBps / 10_000`), which truncates to zero whenever the product is smaller than the denominator. Because the only input guard is `amount == 0`, any depositor can call `deposit()` with a non-zero amount small enough to make the fee round to zero, receiving wrsETH/rsETH while paying no protocol fee.

### Finding Description
In `RSETHPoolV3.viewSwapRsETHAmountAndFee`, the fee for an ETH deposit is:

```solidity
fee = amount * feeBps / 10_000;   // line 300
```

and for a token deposit:

```solidity
fee = amount * feeBps / 10_000;   // line 324
```

The same pattern appears verbatim in `RSETHPoolNoWrapper` (line 301), `RSETHPool` (line 336, using per-token `tokenFeeBps`), and `AGETHPoolV3` (lines 161, 184).

The `deposit()` functions that consume these view functions enforce only `amount == 0` as a lower bound:

```solidity
if (amount == 0) revert InvalidAmount();   // RSETHPoolV3 line 256 / 282
```

No minimum-fee or minimum-amount check exists. Therefore, for any `feeBps` value, there is a non-zero `amount` threshold below which `fee` evaluates to zero:

| feeBps | threshold (fee = 0 when amount <) |
|--------|----------------------------------|
| 1 (0.01 %) | 10 000 |
| 10 (0.1 %) | 1 000 |
| 30 (0.3 %) | 334 |
| 100 (1 %) | 100 |

A depositor who sends exactly `threshold - 1` units receives the full `amount` worth of wrsETH/rsETH with `fee = 0`, bypassing the protocol's intended revenue mechanism entirely.

### Impact Explanation
Protocol fees are the primary revenue stream collected in `feeEarnedInETH` / `feeEarnedInToken` and later withdrawn by the `BRIDGER_ROLE`. Every fee-free deposit permanently reduces the protocol's accrued fee balance. Repeated micro-deposits (or a single deposit just below the threshold) constitute theft of unclaimed yield. The impact is classified as **High – Theft of unclaimed yield**.

The severity is amplified if the pool ever supports tokens with fewer than 18 decimals (e.g., USDC at 6 decimals), because the threshold in human-readable units becomes far more economically meaningful (e.g., with `feeBps = 30`, any USDC deposit below 0.000334 USDC pays zero fee, which is trivially achievable at negligible gas cost relative to the deposited value).

### Likelihood Explanation
The entry path is fully permissionless: any externally-owned account can call `deposit()` with `msg.value = threshold - 1` (for ETH) or approve and call `deposit(token, threshold - 1, ...)` (for ERC-20 tokens). No special role, flash loan, or oracle manipulation is required. For 18-decimal assets the economic incentive per transaction is tiny, but the attack is free to execute and can be batched. If a lower-decimal token is ever added via `addSupportedToken`, the incentive grows substantially.

### Recommendation
Add a post-calculation guard in `viewSwapRsETHAmountAndFee` (and its equivalents) that reverts when the computed fee is zero but `feeBps > 0`:

```solidity
if (feeBps > 0 && fee == 0) revert InvalidAmount();
```

Alternatively, enforce a protocol-wide minimum deposit amount that guarantees `amount * feeBps >= 10_000` for any configured `feeBps`. This mirrors the fix recommended in the referenced report.

### Proof of Concept
1. `RSETHPoolV3` is deployed with `feeBps = 30` (0.3 %).
2. Attacker calls `deposit{value: 333}("")` (333 wei of ETH).
3. `viewSwapRsETHAmountAndFee(333)` computes `fee = 333 * 30 / 10_000 = 9990 / 10_000 = 0`.
4. `feeEarnedInETH += 0` — no fee is recorded.
5. Attacker receives `333 * 1e18 / rsETHToETHrate` wrsETH tokens with zero fee paid.
6. Step 2–5 can be repeated in a loop or batched via a contract; each iteration extracts value from the protocol's expected fee revenue.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L254-265)
```text
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L281-293)
```text
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
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

**File:** contracts/pools/RSETHPoolV3.sol (L323-325)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L299-302)
```text
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-162)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L183-185)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```
