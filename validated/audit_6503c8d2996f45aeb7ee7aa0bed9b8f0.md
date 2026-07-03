Audit Report

## Title
Missing Zero-Amount Guard on Token Deposit Enables Permanent Freezing of User Funds — (`contracts/agETH/AGETHPoolV3.sol`)

## Summary

`AGETHPoolV3.deposit(address token, uint256 amount, string referralId)` transfers the caller's tokens into the pool before computing the agETH output. The token-path formula in `viewSwapAgETHAmountAndFee(amount, token)` performs integer division without a `1e18` scaling factor, causing `agETHAmount` to round to zero for small deposits. When this occurs, `agETH.mint(msg.sender, 0)` is a no-op, the user's tokens are permanently locked in the pool, and no user-facing recovery path exists.

## Finding Description

**Exact code path:**

1. `deposit(token, amount, referralId)` at line 143 checks only `amount == 0`, not the computed output.
2. Line 145: `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` — tokens leave the user unconditionally.
3. Line 147: `viewSwapAgETHAmountAndFee(amount, token)` is called.
4. Line 194: `agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate` — integer division with no `1e18` scaling factor.
5. Line 151: `agETH.mint(msg.sender, agETHAmount)` — mints 0 when `agETHAmount` rounded to zero; OpenZeppelin's `mint(addr, 0)` is a no-op.

**Root cause — asymmetric scaling:**

The ETH path (line 168) correctly scales: `agETHAmount = amountAfterFee * 1e18 / agETHToETHrate`, because `amountAfterFee` is already in wei (18-decimal). The token path (line 194) omits this `1e18` factor. For tokens whose oracle rate (`tokenToETHRate`) is materially smaller than `agETHToETHrate` (~1e18), the numerator `amountAfterFee * tokenToETHRate` can be less than `agETHToETHrate`, flooring to zero.

**Zero-output condition:**
```
amountAfterFee * tokenToETHRate < agETHToETHrate
```

For USDC (6 decimals, `tokenToETHRate ≈ 3.33e14`, `agETHToETHrate ≈ 1.05e18`):
- Threshold: `amount < 1.05e18 / 3.33e14 ≈ 3154` USDC units (~$0.003)

**Why existing checks are insufficient:**

- `if (amount == 0) revert InvalidAmount()` (line 143) validates raw input, not computed output.
- `addSupportedToken` only rejects oracles returning exactly `0` (line 279); any non-zero `tokenToETHRate` that is sufficiently smaller than `agETHToETHrate` passes.
- `moveAssetsForBridging(token)` (line 234) is gated behind `BRIDGER_ROLE` and transfers to `msg.sender` (the bridger), not the depositor — no user recovery path exists.

## Impact Explanation

**Critical — Permanent freezing of funds.**

Tokens transferred at line 145 are irrecoverable by the depositor. The pool has no user-facing withdrawal or refund function. The `BRIDGER_ROLE`-gated `moveAssetsForBridging` sends funds to the bridger, not the original depositor. The frozen amount scales with `agETHToETHrate / tokenToETHRate`: the lower the token's ETH-denominated oracle rate relative to agETH, the larger the deposit that can be silently consumed with zero agETH minted.

## Likelihood Explanation

- Triggerable by any unprivileged user calling the public `deposit(address, uint256, string)` function.
- No oracle manipulation required — the condition arises from ordinary integer arithmetic on legitimate oracle values.
- No `minAmountOut` parameter exists, so callers have no on-chain slippage protection.
- `viewSwapAgETHAmountAndFee` returns `0` silently; users have no on-chain visibility into the rounding threshold before depositing.
- Repeatable for any supported token whose `tokenToETHRate` is sufficiently smaller than `agETHToETHrate`.

## Recommendation

Add a zero-output guard immediately after computing `agETHAmount` in `deposit(address, uint256, string)`:

```solidity
(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
if (agETHAmount == 0) revert InvalidAmount();
```

Alternatively, add a `minAmountOut` parameter to `deposit` so callers can enforce their own slippage tolerance. Also consider adding the same guard to the ETH deposit path for consistency.

## Proof of Concept

```solidity
// Preconditions:
//   feeBps = 30
//   agETHOracle.getRate() = 1.05e18
//   supportedTokenOracle[USDC].getRate() = 3.33e14

function testZeroMintOnDustDeposit() public {
    uint256 amount = 1000; // 1000 USDC units < 3154 threshold
    vm.prank(user);
    IERC20(usdc).approve(address(pool), amount);

    uint256 agETHBefore = agETH.balanceOf(user);
    vm.prank(user);
    pool.deposit(usdc, amount, "ref");
    uint256 agETHAfter = agETH.balanceOf(user);

    // amountAfterFee = 997; agETHAmount = 997 * 3.33e14 / 1.05e18 = 0
    assertEq(agETHAfter - agETHBefore, 0);                         // 0 agETH minted
    assertEq(IERC20(usdc).balanceOf(address(pool)), amount);        // tokens permanently locked
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L143-153)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L166-168)
```text

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L184-194)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L234-240)
```text
    function moveAssetsForBridging(address token) external onlySupportedToken(token) onlyRole(BRIDGER_ROLE) {
        // withdraw token - fees
        uint256 tokenBalanceMinusFees = IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];

        IERC20(token).safeTransfer(msg.sender, tokenBalanceMinusFees);

        emit AssetsMovedForBridging(tokenBalanceMinusFees, token);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L279-281)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```
