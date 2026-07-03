### Title
Depositor Receives Zero wrsETH/agETH When Deposit Amount Is Below Rounding Threshold — (`contracts/pools/RSETHPoolV3.sol`)

### Summary

All L2 deposit pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`, `RSETHPool`, `RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV2NBA`, `AGETHPoolV3`) accept ETH or ERC-20 tokens from a depositor and mint `wrsETH`/`agETH` in return. The minted amount is computed via integer division. When the deposit amount (after fee) is below the rounding threshold imposed by the current exchange rate, the division truncates to zero. No minimum output guard exists in any `deposit()` function, so the depositor's funds are accepted and retained by the pool while they receive nothing.

### Finding Description

Every pool's `viewSwapRsETHAmountAndFee` computes the output as:

```solidity
// RSETHPoolV3.sol – token variant
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

For the ETH variant the formula is `amountAfterFee * 1e18 / rsETHToETHrate`. [2](#0-1) 

`rsETHToETHrate` starts at `1e18` and grows monotonically as staking rewards accrue. Once it exceeds `1e18`, any deposit where `amountAfterFee * tokenToETHRate < rsETHToETHrate` produces `rsETHAmount = 0` via Solidity integer truncation.

The `deposit()` functions only guard against a zero input amount:

```solidity
if (amount == 0) revert InvalidAmount();
``` [3](#0-2) 

There is no subsequent check that `rsETHAmount > 0` before the token transfer and mint execute:

```solidity
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0
``` [4](#0-3) 

The same pattern is present in the ETH deposit path and in every other pool variant. [5](#0-4) 

The `limitDailyMint` modifier also computes `rsETHAmount` internally but does not revert when it is zero — it simply adds zero to `dailyMintAmount` and proceeds. [6](#0-5) 

### Impact Explanation

A depositor who sends a dust-level amount of ETH or a supported ERC-20 token has their funds transferred into the pool contract (or locked as `msg.value`) while receiving zero `wrsETH`/`agETH`. The pool retains the full deposit (minus any fee, which is also zero for dust amounts). The depositor's funds are permanently lost to them with no recourse. This matches the **"contract fails to deliver promised returns"** impact class (Low).

### Likelihood Explanation

The rounding threshold is `ceil(rsETHToETHrate / tokenToETHRate)`. For ETH deposits with a rate of `1.05e18`, any deposit of 1 wei triggers the issue. For ERC-20 tokens with a rate ratio near 1, the threshold is similarly at the 1-wei level. While individual losses are tiny, the condition is trivially reachable by any unprivileged depositor calling `deposit()` with a small `msg.value` or token amount, with no special preconditions.

### Recommendation

Add a zero-output guard immediately after computing `rsETHAmount` in every `deposit()` function across all pool variants:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

This mirrors the protection already present in `LRTDepositPool._beforeDeposit()`, which enforces `minRSETHAmountExpected` and reverts on zero output. [7](#0-6) 

### Proof of Concept

1. Deploy `RSETHPoolV3` with `feeBps = 0` and an oracle returning `rsETHToETHrate = 1.1e18` (10% appreciation).
2. Call `deposit{value: 1}("")` — sends 1 wei ETH.
3. `viewSwapRsETHAmountAndFee(1)` computes: `fee = 0`, `amountAfterFee = 1`, `rsETHAmount = 1 * 1e18 / 1.1e18 = 0`.
4. `wrsETH.mint(msg.sender, 0)` executes — depositor receives 0 `wrsETH`.
5. The 1 wei ETH remains in the pool contract permanently.

For the token variant, replace step 2 with `deposit(token, 1, "")` — the token is transferred from the caller via `safeTransferFrom` before the zero-output mint occurs. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-124)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-292)
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
```

**File:** contracts/pools/RSETHPoolV3.sol (L300-307)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
