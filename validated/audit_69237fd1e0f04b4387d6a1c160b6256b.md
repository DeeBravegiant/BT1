Audit Report

## Title
Zero agETH Minted for Dust Token Deposits Due to Integer Division Truncation - (File: contracts/agETH/AGETHPoolV3.sol)

## Summary

`AGETHPoolV3.deposit(address token, uint256 amount, string referralId)` transfers the depositor's tokens into the pool before computing the agETH output amount. For dust-level deposits, integer division in `viewSwapAgETHAmountAndFee(amount, token)` truncates `agETHAmount` to zero, and no post-computation guard exists to revert. The depositor's tokens are permanently retained by the pool while they receive zero agETH.

## Finding Description

The token-path deposit function at line 143–151 executes in this order:

1. Guard: `if (amount == 0) revert InvalidAmount();` — only rejects zero input.
2. Transfer in: `IERC20(token).safeTransferFrom(msg.sender, address(this), amount);` — tokens leave the depositor.
3. Compute output: `(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);`
4. Mint: `agETH.mint(msg.sender, agETHAmount);` — called with `agETHAmount = 0`, no revert. [1](#0-0) 

The output formula in `viewSwapAgETHAmountAndFee(uint256, address)` is:

```solidity
agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
``` [2](#0-1) 

Both `tokenToETHRate` and `agETHToETHrate` are 1e18-scaled oracle values. For `amountAfterFee = 1` wei with `tokenToETHRate = 0.9e18` and `agETHToETHrate = 1.1e18`:

```
1 * 0.9e18 / 1.1e18 = 0  (integer truncation)
```

The ETH-path overload correctly scales by `1e18` before dividing (`amountAfterFee * 1e18 / agETHToETHrate`), but the token path does not add this scaling — the 1e18 factors only cancel correctly for amounts ≥ `agETHToETHrate / tokenToETHRate`. For any amount below that threshold, the result truncates to zero. [3](#0-2) 

OpenZeppelin's `_mint` does not revert on `amount = 0` (it only checks `account != address(0)`), so the call succeeds silently.

## Impact Explanation

**Low — Contract fails to deliver promised returns.**

A depositor sending any dust amount of a supported token (specifically any `amount` where `amount * tokenToETHRate / agETHToETHrate == 0`) has their tokens accepted and transferred to the pool but receives zero agETH in return. The depositor's token is permanently retained by the pool with no recourse. The invariant "any non-zero deposit yields a non-zero agETH amount" is violated. The monetary loss is dust-level (sub-wei to a few wei), so this does not rise to theft or freezing of material funds.

## Likelihood Explanation

Likelihood is low. No rational user deliberately deposits 1 wei. However, the path is reachable by any unprivileged external caller without any special conditions beyond using a supported token. Realistic triggers include: a contract integration that computes deposit amounts with rounding errors, an automated script with an off-by-one error, or a user testing the pool with a minimal amount. No privileged access, oracle compromise, or governance action is required.

## Recommendation

Add a post-computation guard in `deposit(address token, ...)` immediately after computing `agETHAmount`:

```solidity
(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
if (agETHAmount == 0) revert InvalidAmount();
```

This ensures the revert occurs before the token transfer (or alternatively, move the transfer after the guard). A stricter fix is to enforce a minimum deposit amount equivalent to at least `agETHToETHrate / tokenToETHRate + 1` wei, mirroring the ETH-path pattern.

## Proof of Concept

```solidity
// Setup:
//   tokenToETHRate  = 0.9e18  (token oracle)
//   agETHToETHrate  = 1.1e18  (agETH oracle)
//   feeBps          = 0
//   amount          = 1 wei

// Step 1: user calls deposit(token, 1, "ref")
// Step 2: safeTransferFrom moves 1 wei of token to pool  ← token lost
// Step 3: viewSwapAgETHAmountAndFee(1, token)
//         amountAfterFee = 1
//         agETHAmount = 1 * 0.9e18 / 1.1e18 = 0
// Step 4: agETH.mint(user, 0)  ← no revert, 0 agETH minted
// Result: pool holds 1 wei of token, user holds 0 agETH
```

A Foundry fuzz test asserting `agETHAmount > 0` for all `amount in [1, agETHToETHrate/tokenToETHRate]` with `tokenToETHRate < agETHToETHrate` will confirm the failure for the full truncation range.

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L143-151)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L167-168)
```text
        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L193-194)
```text
        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```
