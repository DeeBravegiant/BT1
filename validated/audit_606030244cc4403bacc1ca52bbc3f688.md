### Title
Lack of Slippage Protection in L2 Pool `deposit()` Functions Allows Users to Receive Fewer rsETH Tokens Than Expected - (File: contracts/pools/RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolNoWrapper.sol, RSETHPool.sol)

### Summary

All L2 pool `deposit()` functions calculate the rsETH output amount based on the live oracle rate at execution time, but accept no `minRSETHAmountExpected` parameter. The mainnet `LRTDepositPool` explicitly provides this protection; the L2 pools do not. When the oracle rate is updated between a user's transaction submission and its on-chain inclusion, the user receives fewer rsETH (or wrsETH) tokens than the `viewSwapRsETHAmountAndFee` quote they relied upon.

### Finding Description

`LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` both accept a `minRSETHAmountExpected` argument and enforce it inside `_beforeDeposit()`:

```solidity
// LRTDepositPool.sol L667-668
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

Every L2 pool `deposit()` function omits this guard entirely. The ETH-deposit path in `RSETHPoolV3`:

```solidity
// RSETHPoolV3.sol L246-264
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minimum check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

`viewSwapRsETHAmountAndFee` derives the output purely from the live oracle rate:

```solidity
// RSETHPoolV3.sol L299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The same pattern is present in:
- `RSETHPoolV3ExternalBridge.deposit()` (ETH and token overloads)
- `RSETHPoolV3WithNativeChainBridge.deposit()` (ETH and token overloads)
- `RSETHPoolNoWrapper.deposit()` (ETH and token overloads)
- `RSETHPool.deposit()` (ETH and token overloads)
- `RSETHPoolV2.deposit()` / `RSETHPoolV2ExternalBridge.deposit()` / `RSETHPoolV2NBA.deposit()`

The oracle rate (`rsETHToETHrate`) is updated regularly as EigenLayer staking rewards accrue. Because rsETH is yield-bearing, the rate monotonically increases. Any oracle update that lands in the same block or a block before the user's transaction will silently reduce the rsETH minted to the user below the amount the `viewSwapRsETHAmountAndFee` quote showed them.

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

When the oracle rate rises between quote time and execution time, the user receives fewer wrsETH/rsETH tokens than the on-chain view function indicated. Because each token is worth proportionally more ETH, the user's deposited ETH value is preserved in aggregate, but the contract does not honour the token quantity the user was shown. Users who rely on the quoted amount for downstream operations (e.g., providing liquidity, collateralising a loan) may find themselves short of the expected token balance.

### Likelihood Explanation

**Medium.** The rsETH oracle rate is updated on a regular cadence (at minimum daily, often more frequently). L2 chains with variable block times or sequencer congestion increase the window between transaction submission and inclusion. Any user who previews a quote via `viewSwapRsETHAmountAndFee` and then submits a deposit is exposed to this discrepancy without any on-chain recourse.

### Recommendation

Add a `minRSETHAmountExpected` parameter to every public `deposit()` overload in all L2 pool contracts, mirroring the pattern already used in `LRTDepositPool._beforeDeposit()`:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply the same change to the token-deposit overloads in `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`, and all V2 variants.

### Proof of Concept

1. User calls `RSETHPoolV3.viewSwapRsETHAmountAndFee(1 ether)` and sees they will receive `X` wrsETH at the current oracle rate `R`.
2. User submits `RSETHPoolV3.deposit{value: 1 ether}("ref")`.
3. Before the transaction is included, the rsETH oracle is updated to rate `R' > R` (normal reward accrual).
4. The deposit executes: `rsETHAmount = (1 ether - fee) * 1e18 / R'`, which is strictly less than `X`.
5. User receives fewer wrsETH tokens than quoted with no on-chain protection and no revert.

**Contrast with mainnet:** calling `LRTDepositPool.depositETH{value: 1 ether}(X, "ref")` with the same quoted minimum would revert at step 4 via `MinimumAmountToReceiveNotMet`, protecting the user. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-92)
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
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-329)
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
