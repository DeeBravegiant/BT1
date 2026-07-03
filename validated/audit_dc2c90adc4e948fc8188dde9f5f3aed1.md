### Title
Zero agETH Minted for Dust Token Deposits Due to Integer Division Truncation - (`contracts/agETH/AGETHPoolV3.sol`)

### Summary

`AGETHPoolV3.deposit(address token, uint256 amount, string referralId)` accepts any non-zero token amount but contains no guard ensuring the computed `agETHAmount > 0`. For dust-level deposits (1 wei), integer division in `viewSwapAgETHAmountAndFee` truncates to zero, causing the token to be transferred in while zero agETH is minted.

### Finding Description

The token-path swap formula in `viewSwapAgETHAmountAndFee` is:

```solidity
agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
``` [1](#0-0) 

Compare with the ETH path, which correctly scales by `1e18` before dividing:

```solidity
agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
``` [2](#0-1) 

Both `tokenToETHRate` and `agETHToETHrate` are 1e18-scaled oracle values. For the token path, the 1e18 factors cancel correctly for normal amounts (e.g., 1 full token = 1e18 wei gives a correct result). However, for `amountAfterFee = 1` wei:

```
agETHAmount = 1 * 0.9e18 / 1.1e18 = 0  (integer truncation)
```

The truncation threshold is `amountAfterFee < agETHToETHrate / tokenToETHRate`. When `tokenToETHRate < agETHToETHrate` (e.g., 0.9e18 vs 1.1e18), this threshold is ~1.22, meaning any 1-wei deposit yields `agETHAmount = 0`.

The `deposit` function only guards against `amount == 0`, not against a zero computed output:

```solidity
if (amount == 0) revert InvalidAmount();

IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

feeEarnedInToken[token] += fee;

agETH.mint(msg.sender, agETHAmount);  // called with 0
``` [3](#0-2) 

OpenZeppelin's `_mint` does not revert on `amount = 0` (it only checks `account != address(0)`), so the call succeeds silently, the token is retained by the pool, and the depositor receives nothing.

### Impact Explanation

A depositor sending 1 wei of a supported token has their token accepted and transferred to the pool, but receives 0 agETH in return. The invariant "any non-zero deposit must yield a non-zero agETH amount" is violated. The depositor loses their token (dust value), and the pool retains it. This matches the **Low** scope: the contract fails to deliver promised returns.

### Likelihood Explanation

Likelihood is low. No rational user deliberately deposits 1 wei. However, it can occur via:
- A contract integration that computes a deposit amount with rounding
- A user testing the pool with a minimal amount
- An automated script with an off-by-one error

No privileged access or external compromise is required — the path is open to any caller.

### Recommendation

Add a post-computation guard in `deposit(address token, ...)` to revert if `agETHAmount == 0`:

```solidity
(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
if (agETHAmount == 0) revert InvalidAmount();
```

Alternatively, apply the same pattern as the ETH path and enforce a minimum deposit amount.

### Proof of Concept

```solidity
// Preconditions:
//   tokenToETHRate = 0.9e18  (token oracle: 1 token = 0.9 ETH)
//   agETHToETHrate = 1.1e18  (agETH oracle: 1 agETH = 1.1 ETH)
//   feeBps = 0 (for simplicity)
//   amount = 1 wei

uint256 amountAfterFee = 1;
uint256 tokenToETHRate  = 0.9e18;
uint256 agETHToETHrate  = 1.1e18;

uint256 agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
// = 1 * 900000000000000000 / 1100000000000000000
// = 900000000000000000 / 1100000000000000000
// = 0  ← integer truncation

// deposit() proceeds:
// token.safeTransferFrom(user, pool, 1)  ← 1 wei transferred in
// agETH.mint(user, 0)                    ← 0 agETH minted, no revert
// user loses 1 wei of token
```

A fuzz test asserting `agETHAmount > 0` for all `amount in [1, agETHToETHrate/tokenToETHRate]` with `tokenToETHRate < agETHToETHrate` will confirm the failure for `amount = 1`.

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
