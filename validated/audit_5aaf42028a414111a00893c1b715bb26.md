### Title
Token Depositor Receives Zero rsETH While Tokens Are Permanently Transferred Due to Rounding Truncation - (File: contracts/pools/RSETHPool.sol)

### Summary
In all L2 pool deposit functions, a user's ERC20 tokens are transferred to the pool **before** the rsETH output amount is computed. When the deposit amount is small enough that integer division truncates `rsETHAmount` to zero, the user permanently loses their tokens while receiving nothing in return. No zero-output guard exists in any of the affected pool contracts.

### Finding Description
The `deposit(address token, uint256 amount, string memory referralId)` function in `RSETHPool.sol` (and identically in `RSETHPoolV3.sol`, `RSETHPoolNoWrapper.sol`, and `RSETHPoolV3ExternalBridge.sol`) follows this sequence:

1. Transfer tokens from user to pool.
2. Compute `rsETHAmount` via `viewSwapRsETHAmountAndFee`.
3. Transfer `rsETHAmount` of rsETH to user.

The critical computation inside `viewSwapRsETHAmountAndFee` is:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

This is integer division. When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, the result truncates to `0`. There is no subsequent check that `rsETHAmount > 0` before the transfer-in has already occurred. The user's tokens are irrecoverably in the pool, and they receive zero rsETH.

The same truncation applies to ETH deposits:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

If `rsETHToETHrate > 1e18` (rsETH has appreciated), even a 1-wei ETH deposit yields `rsETHAmount = 0`.

Affected functions and lines:

- `RSETHPool.sol`: `deposit(address,uint256,string)` — token transferred at line 296, rsETHAmount computed at line 298, zero-guard absent.
- `RSETHPoolV3.sol`: `deposit(address,uint256,string)` — token transferred at line 284, rsETHAmount computed at line 286.
- `RSETHPoolNoWrapper.sol`: `deposit(address,uint256,string)` — token transferred at line 262, rsETHAmount computed at line 264.
- `RSETHPoolV3ExternalBridge.sol`: `deposit(address,uint256,string)` — token transferred at line 403, rsETHAmount computed at line 405.

### Impact Explanation
A user who deposits a dust amount of a supported token (below the rounding threshold for the given oracle rates) will have their tokens permanently transferred to the pool while receiving 0 rsETH. The tokens are not recoverable by the user. This is a direct, permanent loss of user funds. The pool accumulates these tokens and they are eventually bridged to L1, making recovery impossible.

Impact: **Low — Contract fails to deliver promised returns, but doesn't lose value** (user loses deposit; protocol retains the tokens).

### Likelihood Explanation
This is realistic under normal operating conditions:

- For a supported token with `tokenToETHRate = 1e15` (e.g., a token worth 0.001 ETH) and `rsETHToETHrate = 1.1e18`, the zero-output threshold is any deposit where `amount < rsETHToETHrate / tokenToETHRate ≈ 1100` token-wei. Any deposit of fewer than 1100 wei of such a token silently yields 0 rsETH.
- For ETH deposits, once rsETH has appreciated above 1:1 with ETH (`rsETHToETHrate > 1e18`), a 1-wei ETH deposit yields 0 rsETH.
- Users sending dust amounts, testing integrations, or making rounding errors are realistic victims.
- No front-running or privileged access is required; any unprivileged depositor can trigger this by calling `deposit` with a small amount.

### Recommendation
Add a zero-output guard immediately after computing `rsETHAmount` in every deposit function across all pool contracts:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
if (rsETHAmount == 0) revert InvalidAmount();
```

This mirrors the existing `if (amount == 0) revert InvalidAmount()` guard and ensures the user's tokens are never taken without a corresponding rsETH output.

### Proof of Concept

**Setup:**
- `rsETHToETHrate = 1.1e18` (rsETH has appreciated 10% over ETH — normal over time)
- Supported token with `tokenToETHRate = 1e15` (token worth 0.001 ETH)
- `feeBps = 0` for simplicity

**Steps:**
1. User calls `deposit(token, 1000, "ref")` — depositing 1000 wei of the supported token.
2. `RSETHPool.sol` line 296: `IERC20(token).safeTransferFrom(msg.sender, address(this), 1000)` — tokens leave user's wallet.
3. `viewSwapRsETHAmountAndFee(1000, token)` computes:
   - `amountAfterFee = 1000`
   - `rsETHAmount = 1000 * 1e15 / 1.1e18 = 1e18 / 1.1e18 = 0` (integer truncation)
4. `RSETHPool.sol` line 302: `IERC20(address(wrsETH)).safeTransfer(msg.sender, 0)` — user receives nothing.
5. User has permanently lost 1000 wei of the token with no rsETH minted.

The transaction succeeds without revert. The user's tokens are now in the pool's balance, counted as bridgeable assets, and will be sent to L1 — permanently out of the user's reach. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPool.sol (L294-305)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPool.sol (L326-347)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L282-293)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L260-271)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L401-412)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-453)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
