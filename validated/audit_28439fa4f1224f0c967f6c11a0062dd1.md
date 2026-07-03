### Title
No Minimum Output (Slippage) Protection in L2 Pool `deposit()` Functions - (`contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV2ExternalBridge.sol`)

---

### Summary

All L2 pool `deposit()` functions allow users to swap ETH or supported tokens (e.g., wstETH) for rsETH/wrsETH without any minimum output parameter. Users cannot specify the minimum amount of rsETH they expect to receive, leaving them fully exposed to oracle rate changes between transaction submission and execution. The L1 `LRTDepositPool` explicitly provides this protection via `minRSETHAmountExpected`, making the omission in the L2 pools a clear inconsistency with the protocol's own design intent.

---

### Finding Description

The L2 pool contracts expose public `deposit()` functions that compute the rsETH output amount entirely from an on-chain oracle rate at execution time, with no caller-supplied minimum output guard:

**`RSETHPoolNoWrapper.deposit(string referralId)`** (ETH path): [1](#0-0) 

**`RSETHPoolNoWrapper.deposit(address token, uint256 amount, string referralId)`** (token path): [2](#0-1) 

**`RSETHPoolV3ExternalBridge.deposit(string referralId)`** (ETH path): [3](#0-2) 

**`RSETHPoolV3ExternalBridge.deposit(address token, uint256 amount, string referralId)`** (token path): [4](#0-3) 

**`RSETHPoolV2ExternalBridge.deposit(string referralId)`**: [5](#0-4) 

**`RSETHPool.deposit(string referralId)`** and **`RSETHPool.deposit(address token, uint256 amount, string referralId)`**: [6](#0-5) 

In every case, the rsETH output is computed as:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

where `rsETHToETHrate` is read from `IOracle(rsETHOracle).getRate()` at execution time, with no floor check against any caller-supplied minimum. [7](#0-6) 

By contrast, the L1 `LRTDepositPool` explicitly accepts and enforces `minRSETHAmountExpected` in both `depositETH` and `depositAsset`: [8](#0-7) [9](#0-8) 

The oracle implementations available in the repo include Chainlink-based feeds (`ChainlinkPriceOracle.sol`) and protocol-specific feeds (`RSETHPriceFeed.sol`).


For the token deposit path (e.g., wstETH), the output depends on **two** oracle reads — `rsETHToETHrate` and `tokenToETHRate` — compounding the exposure: [10](#0-9) 

---

### Impact Explanation

**Impact: Low — Contract fails to deliver promised returns without losing principal.**

Users who simulate the expected rsETH output off-chain and then submit a `deposit()` transaction have no on-chain guarantee that the output will match their expectation. If the oracle rate is updated (e.g., a Chainlink heartbeat push) between tx submission and inclusion, the user silently receives fewer rsETH tokens than anticipated with no revert path. For the token deposit path, two oracle values must both remain stable, doubling the exposure window. Unlike the L1 pool, there is no mechanism for users to protect themselves.

---

### Likelihood Explanation

Oracle rate updates on L2s (Chainlink heartbeat or deviation-triggered pushes) are routine and occur independently of user transactions. On chains with public mempools, a pending `deposit()` transaction is visible to searchers who can front-run an oracle update to ensure the user's transaction executes at a worse rate. The affected functions are the primary user-facing entry points on every deployed L2 chain, so every depositor is exposed on every deposit.

---

### Recommendation

Add a `minRsETHAmountExpected` parameter to all `deposit()` overloads in every L2 pool contract, mirroring the pattern already used in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert InsufficientOutput();
    ...
}
```

Apply the same pattern to the token deposit overload, checking the computed `rsETHAmount` against the caller-supplied minimum before transferring or minting.

---

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees they will receive `X` wrsETH at the current oracle rate.
2. User submits `RSETHPoolV3ExternalBridge.deposit{value: 1 ether}("ref")`.
3. Before the tx is included, a Chainlink oracle push increases `rsETHToETHrate` (rsETH appreciated vs ETH), so the same 1 ETH now buys fewer rsETH units.
4. The `deposit()` function executes with the new rate, minting `X - delta` wrsETH to the user.
5. The user has no recourse — there is no minimum output check, no revert, and no refund. The shortfall `delta` is permanently lost relative to the user's expectation.

On chains with a public mempool, a searcher can deliberately front-run the oracle update with the user's pending deposit to guarantee the user executes at the worse rate, extracting the difference as MEV.

### Citations

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-301)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPool.sol (L265-305)
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

    /// @dev Swaps token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
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
