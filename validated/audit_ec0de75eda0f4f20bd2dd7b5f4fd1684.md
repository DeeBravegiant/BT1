### Title
Missing Minimum Output Protection in Pool `deposit` Functions Exposes Users to Oracle Rate Slippage - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
All L2 RSETHPool `deposit` functions lack a `minRsETHAmountOut` parameter. The rsETH amount minted is computed at execution time from a live oracle rate, with no floor the caller can enforce. If the oracle rate changes between transaction submission and on-chain execution, users silently receive fewer rsETH tokens than they expected with no ability to revert.

### Finding Description
Every `deposit` entry point across the RSETHPool family computes the output amount exclusively from the oracle rate at execution time and immediately mints/transfers that amount to the caller, with no caller-supplied minimum:

```solidity
// RSETHPoolV3ExternalBridge.sol – ETH deposit
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(...) {
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);   // rate-dependent
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minOut check
    ...
}
``` [1](#0-0) 

The same pattern appears in the token-deposit overload: [2](#0-1) 

And identically in `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3`, and `RSETHPoolV3WithNativeChainBridge`: [3](#0-2) [4](#0-3) [5](#0-4) 

The output is computed by `viewSwapRsETHAmountAndFee`, which divides the input by the live oracle rate:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [6](#0-5) 

The oracle rate for the rsETH/ETH pair is fetched from `rsETHOracle` (e.g. `InterimRSETHOracle` or a cross-chain rate receiver), and the collateral token rate from `ChainlinkOracleForRSETHPoolCollateral`: [7](#0-6) 

Notably, `ChainlinkOracleForRSETHPoolCollateral` checks `answeredInRound < roundID` but does **not** check `block.timestamp - updatedAt` against a heartbeat, meaning a price that was last updated hours ago is accepted as fresh. A Chainlink deviation-threshold update that fires in the same block as a user deposit will silently change the output amount with no protection for the user.

By contrast, the L1 `LRTDepositPool` correctly exposes a `minRSETHAmountExpected` parameter and reverts if the computed amount falls below it: [8](#0-7) 

### Impact Explanation
A user who submits a deposit transaction expecting `X` rsETH receives `Y < X` rsETH with no recourse. Because the rsETH/ETH rate can move between block submission and inclusion (Chainlink deviation update, cross-chain rate push via `MultiChainRateProvider.updateRate()` which is publicly callable, or natural block-ordering), the user's actual output is unpredictable and unenforceable. The user does not lose ETH value in absolute terms, but the contract fails to deliver the promised token quantity — matching the **Low** impact tier: *"Contract fails to deliver promised returns, but doesn't lose value."*

### Likelihood Explanation
Chainlink feeds update on deviation thresholds (typically 0.5 %–1 %) and heartbeat intervals. On active L2s (Arbitrum, Optimism, Base) where these pools are deployed, rate updates occur multiple times per day. Any deposit transaction that lands in the same block as a Chainlink update, or after a public `updateRate()` call on the cross-chain rate provider, will silently receive a different output than the user simulated off-chain. No privileged access is required; the trigger is ordinary oracle operation.

### Recommendation
Add a `minRsETHAmountOut` parameter to every `deposit` overload and revert if the computed amount falls below it, mirroring the pattern already used in `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountOut)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountOut) revert SlippageExceeded();
    ...
}
```

Additionally, add a heartbeat/freshness check to `ChainlinkOracleForRSETHPoolCollateral.getRate()` (e.g. `if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice()`).

### Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain at block N and sees they will receive `1000 rsETH`.
2. User submits `deposit{value: 1 ether}("ref")` targeting block N+1.
3. In block N+1, a Chainlink keeper updates the wstETH/ETH feed (0.5 % deviation), raising `rsETHToETHrate`.
4. `viewSwapRsETHAmountAndFee` now returns `995 rsETH`.
5. The contract mints `995 rsETH` to the user — 0.5 % less than expected — with no revert and no way for the user to have prevented it.

The same scenario applies to the token-deposit path where `tokenToETHRate` from `ChainlinkOracleForRSETHPoolCollateral` is used: [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L390-412)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-453)
```text
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

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-301)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
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
