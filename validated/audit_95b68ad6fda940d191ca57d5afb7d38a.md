### Title
Missing User-Supplied Minimum Output Amount in L2 Pool Deposit Functions - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPool.sol`)

### Summary

All L2 pool `deposit()` functions lack a `minRsETHAmount` (minimum output) parameter, giving depositors no on-chain slippage protection. The L1 `LRTDepositPool` correctly enforces `minRSETHAmountExpected`, but the analogous protection is entirely absent from every L2 pool variant.

### Finding Description

Every L2 pool `deposit()` function computes the rsETH/wrsETH output solely from the live oracle rate at execution time and immediately mints that amount, with no caller-supplied floor:

`RSETHPoolV3.sol` ETH deposit: [1](#0-0) 

`RSETHPoolV3.sol` token deposit: [2](#0-1) 

The same pattern is repeated verbatim in `RSETHPoolV3ExternalBridge.sol`: [3](#0-2) 

And in `RSETHPoolNoWrapper.sol`: [4](#0-3) 

And in `RSETHPool.sol`: [5](#0-4) 

By contrast, the L1 `LRTDepositPool` accepts and enforces a `minRSETHAmountExpected` parameter:

```solidity
function depositETH(uint256 minRSETHAmountExpected, ...) external payable ...
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...) external ...
``` [6](#0-5) 

The slippage check in `_beforeDeposit`: [7](#0-6) 

The oracle rate used in all L2 pools is fetched live at execution time: [8](#0-7) 

### Impact Explanation

A user calls `viewSwapRsETHAmountAndFee()` off-chain to preview their expected rsETH output, then submits a `deposit()` transaction. If the oracle rate is updated (rsETH appreciates in ETH terms) between the preview and execution — whether by a scheduled protocol update or by a transaction ordering coincidence — the user receives fewer rsETH/wrsETH tokens than they anticipated, with no on-chain mechanism to revert the transaction. The user's ETH or LST is consumed at a worse rate than expected, with no recourse.

**Impact**: Low — Contract fails to deliver promised returns, but does not directly lose the deposited value (the user still receives rsETH at the current oracle rate, just not the rate they previewed).

### Likelihood Explanation

The rsETH oracle rate increases over time as staking rewards accrue. Oracle updates are routine protocol operations. Any deposit submitted in the same block as or shortly after an oracle rate update will silently receive fewer rsETH than the user previewed. This is a normal operating condition, not an edge case, making the likelihood **Medium**.

### Recommendation

Add a `minRsETHAmountExpected` parameter to all L2 pool `deposit()` functions, mirroring the L1 `LRTDepositPool` pattern:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    wrsETH.mint(msg.sender, rsETHAmount);
    ...
}
```

Apply the same pattern to the token `deposit(address, uint256, string)` overloads in all four pool contracts.

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPoolV3` and sees they will receive `X` wrsETH at the current oracle rate.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the user's transaction is included, the protocol updates the rsETH oracle rate (rsETH is now worth more ETH), which is a routine operation.
4. The user's `deposit()` executes with the new, higher rate: `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` yields fewer tokens than `X`.
5. The user receives `Y < X` wrsETH with no revert, no warning, and no recourse, because no `minRsETHAmountExpected` check exists in any L2 pool `deposit()` function. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-412)
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

    /// @dev Swaps supported token for rsETH
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-271)
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

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
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

**File:** contracts/LRTDepositPool.sol (L76-118)
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

    /// @notice helps user stake LST to the protocol
    /// @param asset LST asset address to stake
    /// @param depositAmount LST asset amount to stake
    /// @param minRSETHAmountExpected Minimum amount of rseth to receive
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

**File:** contracts/LRTDepositPool.sol (L665-670)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
