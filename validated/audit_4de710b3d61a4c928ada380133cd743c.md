### Title
Fee-on-Transfer Token Deposit Inflates wrsETH Minting, Causing Protocol Insolvency - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The `deposit(address token, uint256 amount, ...)` function in the L2 pool contracts uses the caller-supplied `amount` parameter—not the actual tokens received—to calculate and mint wrsETH. When a fee-on-transfer token is used, the pool receives fewer tokens than `amount`, but mints wrsETH as if the full `amount` arrived, making the pool permanently undercollateralized.

### Finding Description
In `RSETHPoolV3.sol`, the token deposit path is:

```solidity
// Line 284
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

// Line 286 — uses `amount`, not actual received balance
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

feeEarnedInToken[token] += fee;

// Line 290 — mints based on inflated `amount`
wrsETH.mint(msg.sender, rsETHAmount);
``` [1](#0-0) 

`viewSwapRsETHAmountAndFee` computes the rsETH amount purely from the `amount` argument:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

If the token deducts a transfer fee, the pool receives `amount - transferFee` tokens but mints wrsETH corresponding to the full `amount`. The same pattern is present in `RSETHPoolV3ExternalBridge.sol`: [3](#0-2) 

And in `RSETHPool.sol`: [4](#0-3) 

And in `RSETHPoolNoWrapper.sol`: [5](#0-4) 

None of these functions measure the balance delta before/after the `safeTransferFrom` call to determine the true received amount.

### Impact Explanation
Every deposit with a fee-on-transfer token mints more wrsETH than the underlying token value justifies. The pool accumulates a growing shortfall between its token holdings and the wrsETH it has issued. When the bridger calls `moveAssetsForBridging` to send tokens to L1 for restaking, fewer tokens are available than the outstanding wrsETH implies. This directly causes **protocol insolvency**: honest depositors who deposited non-fee tokens will find the pool unable to back all outstanding wrsETH, resulting in permanent loss of funds for some users.

### Likelihood Explanation
The protocol's `addSupportedToken` admin function can whitelist any ERC20. Several real tokens (e.g., USDT on some chains, STA, PAXG, tokens with configurable fees) implement transfer fees. If any such token is ever added as a supported deposit asset, every deposit through it silently inflates wrsETH supply. The attacker's entry path is simply calling the public `deposit(token, amount, referralId)` function with a fee-on-transfer token—no special role or privilege is required beyond the token being whitelisted.

### Recommendation
Replace the use of the caller-supplied `amount` with the actual received amount, measured as the balance difference before and after the transfer:

```solidity
uint256 balanceBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(token).balanceOf(address(this)) - balanceBefore;

(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(actualReceived, token);
```

Apply this fix consistently across `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPool.sol`, and `RSETHPoolNoWrapper.sol`.

### Proof of Concept
1. A fee-on-transfer token `FeeToken` (2% transfer fee) is added as a supported token in `RSETHPoolV3`.
2. Attacker calls `deposit(FeeToken, 1000e18, "")`.
3. `safeTransferFrom` moves 1000e18 from attacker; pool receives 980e18 (2% fee taken by token).
4. `viewSwapRsETHAmountAndFee(1000e18, FeeToken)` computes rsETH for 1000e18 (minus pool's own protocol fee), not 980e18.
5. `wrsETH.mint(attacker, rsETHAmount)` mints wrsETH backed by 1000e18 worth of value, but only 980e18 of tokens exist in the pool.
6. Repeated deposits widen the insolvency gap. When `moveAssetsForBridging` is called, the pool transfers tokens to L1 but the total wrsETH outstanding exceeds the token backing, causing losses for honest depositors when they attempt to redeem. [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L401-411)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPool.sol (L296-304)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L262-270)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```
