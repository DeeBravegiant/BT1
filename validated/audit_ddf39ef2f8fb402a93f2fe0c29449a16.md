I have the full contract code. Let me analyze the exact vulnerability path.

### Title
Missing Zero-Amount Guard in Token Deposit Causes Permanent Freezing of User Funds — (`contracts/agETH/AGETHPoolV3.sol`)

---

### Summary

`AGETHPoolV3.deposit(token, amount, referralId)` transfers the user's tokens into the pool **before** computing the agETH output amount. When integer division in `viewSwapAgETHAmountAndFee` rounds `agETHAmount` to zero, the contract calls `agETH.mint(user, 0)` — a no-op — while the user's tokens remain permanently locked in the pool with no user-facing recovery path.

---

### Finding Description

The token deposit flow in `AGETHPoolV3` is:

```
deposit(token, amount, referralId)
  → safeTransferFrom(msg.sender, address(this), amount)   // line 145 — tokens leave user
  → viewSwapAgETHAmountAndFee(amount, token)              // line 147
      agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate  // line 194
  → agETH.mint(msg.sender, agETHAmount)                   // line 151 — mints 0 if rounded down
``` [1](#0-0) 

The formula at line 194 performs integer (floor) division with no `1e18` scaling factor — unlike the ETH path at line 168 which multiplies by `1e18` before dividing: [2](#0-1) [3](#0-2) 

`agETHAmount` rounds to zero whenever:

```
amountAfterFee * tokenToETHRate < agETHToETHrate
```

There is **no** `require(agETHAmount > 0)` guard anywhere in the deposit path. The only input check is `if (amount == 0) revert InvalidAmount()` (line 143), which validates the raw input, not the computed output. [4](#0-3) 

The `addSupportedToken` guard only rejects oracles returning exactly `0`: [5](#0-4) 

Any non-zero `tokenToETHRate` that is sufficiently smaller than `agETHToETHrate` passes this check and enables the rounding-to-zero condition.

**Concrete threshold example (USDC, 6 decimals, ETH at $3 000):**
- `tokenToETHRate ≈ 3.33 × 10¹⁴` (1 USDC unit in ETH wei)
- `agETHToETHrate ≈ 1.05 × 10¹⁸`
- Zero-output threshold: deposits `< 3 154 USDC units` (≈ $0.003) mint 0 agETH

For tokens with lower ETH-denominated rates the threshold rises proportionally, potentially reaching economically meaningful amounts.

---

### Impact Explanation

Once tokens are transferred in and 0 agETH is minted, the user has no recourse:

- There is no user-facing withdrawal or refund function.
- `moveAssetsForBridging(token)` (line 234) is gated behind `BRIDGER_ROLE` and sends tokens to `msg.sender` (the bridger), **not** back to the depositor. [6](#0-5) 

The deposited tokens are permanently frozen from the user's perspective. Impact severity scales with `agETHToETHrate / tokenToETHRate`: the lower the token's ETH value relative to agETH, the larger the deposit that can be silently consumed.

---

### Likelihood Explanation

- Any supported token whose ETH rate is materially lower than `agETHToETHrate` (e.g., stablecoins, low-value tokens) creates a non-zero rounding threshold.
- No oracle manipulation is required — the condition arises from ordinary integer arithmetic on legitimate oracle values.
- Users have no on-chain visibility into the rounding threshold before depositing; the `viewSwapAgETHAmountAndFee` view function returns `0` silently.
- The absence of a `minAmountOut` parameter means there is no slippage protection the caller can set.

---

### Recommendation

Add a zero-output guard immediately after computing `agETHAmount` in both the token deposit path and the ETH deposit path:

```solidity
if (agETHAmount == 0) revert InvalidAmount();
```

Alternatively, add a `minAmountOut` parameter to `deposit` so callers can enforce their own slippage tolerance.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Assume AGETHPoolV3 is deployed with:
//   feeBps = 30 (0.3%)
//   agETHOracle.getRate() = 1.05e18
//   supportedTokenOracle[USDC].getRate() = 3.33e14  (1 USDC unit ≈ 0.000000333 ETH)

// User deposits 1000 USDC units (0.001 USDC):
//   amountAfterFee = 1000 * (10_000 - 30) / 10_000 = 997
//   agETHAmount    = 997 * 3.33e14 / 1.05e18
//                  = 3.32e17 / 1.05e18
//                  = 0  (integer division floors to 0)
//
// Result: 1000 USDC units transferred to pool, 0 agETH minted, user has no recovery path.

function testZeroMintOnDustDeposit() public {
    uint256 amount = 1000; // 1000 USDC units
    vm.prank(user);
    IERC20(usdc).approve(address(pool), amount);

    uint256 agETHBefore = agETH.balanceOf(user);
    vm.prank(user);
    pool.deposit(usdc, amount, "ref");
    uint256 agETHAfter = agETH.balanceOf(user);

    assertEq(agETHAfter - agETHBefore, 0);                        // 0 agETH minted
    assertEq(IERC20(usdc).balanceOf(address(pool)), amount);       // tokens stuck in pool
}
```

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

**File:** contracts/agETH/AGETHPoolV3.sol (L160-168)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

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
