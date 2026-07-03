### Title
Zero rsETH Minted on Small Token Deposits Due to Integer Division Truncation - (File: contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
In `RSETHPoolNoWrapper` (and the same pattern in `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`), the `deposit(address token, uint256 amount, string referralId)` function transfers the user's tokens into the pool and then computes `rsETHAmount` via integer division. When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, the division truncates to zero. Because there is no `rsETHAmount > 0` guard, the call succeeds: the user's tokens are permanently absorbed by the pool and they receive 0 rsETH in return.

### Finding Description
`viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes the output as:

```
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

Both `tokenToETHRate` and `rsETHToETHrate` are 1e18-scaled values. When `tokenToETHRate` is small relative to `rsETHToETHrate` (i.e., the deposited token is worth less in ETH than rsETH), and `amountAfterFee` is below the truncation threshold, the result is 0.

The caller `deposit(address token, ...)` does not validate the computed `rsETHAmount` before executing the transfer:

```solidity
IERC20(token).safeTransferFrom(msg.sender, address(this), amount); // tokens leave user
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;
rsETH.safeTransfer(msg.sender, rsETHAmount); // transfers 0 — no revert
``` [2](#0-1) 

The same pattern is present in `RSETHPoolV3.sol`: [3](#0-2) 

and `RSETHPoolV3.sol`'s `viewSwapRsETHAmountAndFee`: [4](#0-3) 

### Impact Explanation
A depositor sends a non-zero token amount that passes the `amount == 0` guard, but the computed `rsETHAmount` truncates to zero. The tokens are irrecoverably transferred into the pool (they join the bridgeable balance, not a user-claimable mapping), and the user receives nothing. This matches the allowed impact: **Low — contract fails to deliver promised returns**.

### Likelihood Explanation
The truncation threshold is `amount < rsETHToETHrate / tokenToETHRate`. For a token whose oracle reports a low ETH value (e.g., a token worth 1e14 in 1e18-scaled ETH, with rsETH at ~1e18), any deposit below ~1e4 token-wei triggers the bug. Any user depositing a small or "dust" amount of a low-value supported token hits this silently. No special permissions are required; the `deposit` function is fully public and `whenNotPaused`. [5](#0-4) 

### Recommendation
Add a zero-output guard immediately after computing `rsETHAmount`:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
if (rsETHAmount == 0) revert InvalidAmount();
```

This mirrors the existing `if (amount == 0) revert InvalidAmount();` guard and prevents the contract from silently consuming user tokens.

### Proof of Concept
Assume:
- `rsETHToETHrate` = 1.05 × 10¹⁸ (rsETH has appreciated 5%)
- `tokenToETHRate` = 1 × 10¹⁴ (token worth 0.0001 ETH)
- `feeBps` = 0
- User calls `deposit(token, 1e4, "ref")`

Calculation:
```
amountAfterFee = 1e4
rsETHAmount    = 1e4 * 1e14 / 1.05e18
               = 1e18 / 1.05e18
               = 0  (integer truncation)
```

Result: `safeTransferFrom` moves `1e4` token-wei from the user into the pool. `safeTransfer(msg.sender, 0)` executes without revert. The user's tokens are gone; they hold no rsETH. [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-270)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-311)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }

    /// @dev view function to get the rsETH amount for a given amount of token
    /// @param amount The amount of token
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
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
```

**File:** contracts/pools/RSETHPoolV3.sol (L282-292)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3.sol (L324-334)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
