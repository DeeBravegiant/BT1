### Title
Missing Zero-Amount Guard After Division-Truncated rsETH Calculation in Token Deposit Path - (File: contracts/pools/RSETHPoolV3.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

Every L2 pool variant computes the rsETH amount for a token deposit as:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

Both rates are in 1e18 precision. When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, Solidity integer division truncates the result to 0. No guard checks whether `rsETHAmount == 0` before the pool transfers the user's tokens in and mints/transfers 0 rsETH out. The deposited tokens are silently retained by the pool.

---

### Finding Description

In every L2 pool variant the token-deposit overload of `viewSwapRsETHAmountAndFee` computes:

```solidity
// RSETHPoolV3.sol L334 (identical in RSETHPool L346, RSETHPoolNoWrapper L311,
// RSETHPoolV3ExternalBridge L451, RSETHPoolV3WithNativeChainBridge L370)
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` is the oracle price of one full token in ETH (1e18 precision), and `rsETHToETHrate` is the rsETH/ETH rate (also 1e18 precision, always ≥ 1e18 per `InterimRSETHOracle._setRate`). The ETH-deposit overload avoids this by explicitly scaling:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // ETH path – correct
```

The token path relies on `tokenToETHRate` providing the same scaling, but performs no zero-check on the result. The calling deposit functions proceed unconditionally:

```solidity
// RSETHPoolV3.sol L284-L292
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0 – user receives nothing
```

The only existing guard is `if (amount == 0) revert InvalidAmount()`, which does not protect against a non-zero `amount` producing a zero `rsETHAmount`.

The L1 `LRTDepositPool` has an analogous formula:

```solidity
// LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

but is protected by `minRSETHAmountExpected` in `_beforeDeposit` (L667). The L2 pool token path has no equivalent protection.

---

### Impact Explanation

When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, the division truncates to 0. The user's tokens are transferred into the pool and the pool mints or transfers 0 rsETH back. The deposited tokens are permanently retained by the pool with no recourse for the user.

For currently supported 18-decimal LSTs (wstETH, stETH, rETH, sfrxETH) with rates close to 1e18, truncation to 0 occurs only when `amountAfterFee = 1` (1 wei of token), since `1 * ~1e18 / ~1.05e18 = 0`. The monetary value lost per incident is negligible (1 wei). The contract fails to deliver its promised return for that input, but the absolute value lost is dust.

**Impact: Low** — Contract fails to deliver promised returns; the deposited amount is not returned to the user.

---

### Likelihood Explanation

Any unprivileged user calling `deposit(address token, uint256 amount, string referralId)` with `amount = 1` on any of the five affected pool contracts triggers the condition. No special setup, front-running, or privileged access is required. The entry path is fully public and permissionless. Likelihood is low in practice because no rational user deposits 1 wei intentionally, but the code path is reachable without any precondition.

---

### Recommendation

Add a zero-amount guard immediately after computing `rsETHAmount` in the token-deposit overload of `viewSwapRsETHAmountAndFee`, or in the calling `deposit` function:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

This mirrors the protection already present in `LRTDepositPool._beforeDeposit` via `minRSETHAmountExpected`, and closes the gap between the ETH and token deposit paths.

---

### Proof of Concept

1. Deploy or interact with `RSETHPoolV3` on any supported L2.
2. Call `deposit(wstETH, 1, "ref")` — depositing 1 wei of wstETH.
3. Inside `viewSwapRsETHAmountAndFee(1, wstETH)`:
   - `fee = 1 * feeBps / 10_000 = 0`
   - `amountAfterFee = 1`
   - `tokenToETHRate = ~1.15e18` (wstETH/ETH)
   - `rsETHToETHrate = ~1.05e18` (rsETH/ETH, always ≥ 1e18)
   - `rsETHAmount = 1 * 1.15e18 / 1.05e18 = 1` (non-zero here, but for `tokenToETHRate < rsETHToETHrate`, e.g. a token at 0.9e18 rate: `1 * 0.9e18 / 1.05e18 = 0`)
4. With `rsETHAmount = 0`, `wrsETH.mint(msg.sender, 0)` executes silently.
5. The user's 1 wei of token is held by the pool; the user receives nothing.

Concretely, for any token whose oracle rate is below the current rsETH/ETH rate (e.g., a newly added collateral at 0.95e18 while rsETH is at 1.05e18), a deposit of 1 wei produces `rsETHAmount = 0` and the token is lost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
