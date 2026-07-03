### Title
L2 Pool `deposit()` Functions Lack Minimum Output Protection, Exposing Depositors to Oracle Rate Slippage - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

Every L2 pool `deposit()` function (ETH and token variants) across `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, and `AGETHPoolV3` accepts user funds and mints rsETH/agETH at the oracle rate read at execution time, with no `minRsETHAmountExpected` parameter. The L1 counterpart `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` both enforce this protection, making the omission on L2 a clear inconsistency that leaves depositors unprotected.

---

### Finding Description

`LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` on L1 both accept a `minRSETHAmountExpected` parameter and revert if the minted amount falls below it:

```solidity
// LRTDepositPool.sol L76-93
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused ...
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
}
```

The L2 pool equivalents have no such parameter. For example, `RSETHPoolV3.deposit(address token, uint256 amount, string referralId)`:

```solidity
// RSETHPoolV3.sol L271-293
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token) limitDailyMint(amount, token)
{
    IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

The output `rsETHAmount` is computed from `getRate()` at execution time:

```solidity
// RSETHPoolV3.sol L324-334
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
uint256 rsETHToETHrate = getRate();
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

If the oracle rate increases between the user's transaction submission and its on-chain execution (e.g., due to a reward accrual update or a routine oracle push), the user receives fewer rsETH tokens than they observed in the preview call, with no ability to revert the transaction.

The same pattern is present in:
- `RSETHPoolNoWrapper.deposit(address, uint256, string)` and `deposit(string)`
- `RSETHPoolV3ExternalBridge.deposit(address, uint256, string)` and `deposit(string)`
- `RSETHPoolV3WithNativeChainBridge.deposit(address, uint256, string)` and `deposit(string)`
- `RSETHPool.deposit(address, uint256, string)` and `deposit(string)`
- `AGETHPoolV3.deposit(address, uint256, string)` and `deposit(string)`

---

### Impact Explanation

A depositor who previews the exchange rate via `viewSwapRsETHAmountAndFee()` before submitting a transaction may receive materially fewer rsETH/agETH tokens than expected if the oracle rate is updated in the same block or a subsequent block before their transaction is included. The user's full input amount is consumed with no recourse. This matches the "contract fails to deliver promised returns" impact class.

**Impact: Low** — Contract fails to deliver promised returns, but does not lose the underlying ETH value.

---

### Likelihood Explanation

Oracle rates for rsETH are updated periodically by authorized operators as rewards accrue. On L2 networks with lower gas costs and higher transaction throughput, the window between a user's `viewSwapRsETHAmountAndFee()` call and their deposit transaction being mined is non-trivial. Any depositor on any supported L2 chain is exposed on every deposit. No special privileges or attack setup are required — the rate simply needs to change between preview and execution, which is a routine protocol event.

---

### Recommendation

Add a `minRsETHAmountExpected` parameter to all `deposit()` overloads in every L2 pool contract, mirroring the protection already present in `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()`. Revert if the computed output falls below the caller-specified minimum:

```solidity
function deposit(address token, uint256 amount, uint256 minRsETHAmountExpected, string memory referralId) external {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    if (rsETHAmount < minRsETHAmountExpected) revert SlippageExceeded();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

---

### Proof of Concept

1. User calls `RSETHPoolV3.viewSwapRsETHAmountAndFee(1 ether)` and observes they will receive `X` rsETH at the current oracle rate.
2. User submits `RSETHPoolV3.deposit{value: 1 ether}(referralId)`.
3. Before the transaction is mined, the oracle operator calls `getRate()` update, increasing the rsETH/ETH rate.
4. User's transaction executes: `viewSwapRsETHAmountAndFee` now returns `Y < X` rsETH.
5. User receives `Y` rsETH — fewer than previewed — with no ability to revert.

The L1 equivalent `LRTDepositPool.depositETH(minRSETHAmountExpected, referralId)` would have reverted at step 4, protecting the user. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L307-329)
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

**File:** contracts/agETH/AGETHPoolV3.sol (L134-154)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPool.sol (L284-305)
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

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```
