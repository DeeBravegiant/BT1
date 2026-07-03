### Title
Missing `minRsETHAmountExpected` Slippage and Deadline Protection in Pool `deposit()` Functions - (File: contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary

The `deposit()` functions across all L2 pool contracts accept ETH or supported tokens and mint/transfer rsETH to the caller using a live oracle rate, but accept no `minRsETHAmountExpected` (slippage) parameter and no `deadline` parameter. A user's transaction can sit in the mempool and execute at a later time when the oracle rate has moved, delivering fewer rsETH tokens than the user anticipated at submission time.

### Finding Description

Every L2 pool `deposit()` entry point computes the rsETH output amount at execution time by querying the oracle rate:

`RSETHPool.sol` `deposit(string referralId)`: [1](#0-0) 

`RSETHPoolNoWrapper.sol` `deposit(string referralId)`: [2](#0-1) 

`RSETHPoolV2ExternalBridge.sol` `deposit(string referralId)`: [3](#0-2) 

`RSETHPoolV3ExternalBridge.sol` `deposit(string referralId)`: [4](#0-3) 

In every case the rsETH amount is derived from `viewSwapRsETHAmountAndFee`, which reads the live oracle rate: [5](#0-4) 

Neither a minimum-output guard nor a deadline is accepted. By contrast, the L1 `LRTDepositPool` explicitly requires `minRSETHAmountExpected` from the caller: [6](#0-5) 

The same gap exists for the token-overloaded `deposit(address token, uint256 amount, string referralId)` variants in `RSETHPool` and `RSETHPoolNoWrapper`: [7](#0-6) [8](#0-7) 

### Impact Explanation

rsETH is a yield-bearing token whose oracle rate (`rsETHToETHrate`) monotonically increases over time as staking rewards accrue. When a user's `deposit()` transaction is delayed in the mempool (e.g., during L2 sequencer congestion or gas-price spikes), the oracle rate at execution time is higher than at submission time. The contract therefore mints/transfers fewer rsETH tokens than the user expected. The user has no on-chain mechanism to reject execution once the rate has moved beyond their acceptable threshold.

**Impact: Low** — the contract fails to deliver the promised rsETH amount; the user's ETH is consumed but they receive fewer rsETH tokens than anticipated. No ETH is stolen, but the user's position is smaller than intended with no recourse.

### Likelihood Explanation

L2 networks (Arbitrum, Unichain, and the other chains hosting these pools) can experience mempool delays and sequencer reordering. Any user who submits a `deposit()` transaction during a period of congestion and whose transaction is included significantly later will silently receive fewer rsETH tokens. No attacker action is required; ordinary network conditions are sufficient.

### Recommendation

1. Add a `uint256 minRsETHAmountExpected` parameter to every `deposit()` overload in all pool contracts, mirroring the pattern already used in `LRTDepositPool::depositETH` and `LRTDepositPool::depositAsset`.
2. Optionally add a `uint256 deadline` parameter and revert if `block.timestamp > deadline`, consistent with the pattern recommended in the referenced report.

### Proof of Concept

1. User calls `RSETHPool.deposit{value: 1 ether}("ref")` when the oracle rate is `1.05e18` (1 rsETH = 1.05 ETH), expecting ≈ `0.952 rsETH`.
2. Transaction sits in the mempool for several hours.
3. Oracle rate updates to `1.06e18` before inclusion.
4. `viewSwapRsETHAmountAndFee` computes `rsETHAmount = 1e18 * 1e18 / 1.06e18 ≈ 0.943 rsETH`.
5. User receives `0.943 rsETH` instead of the `0.952 rsETH` they expected — a ~1% shortfall with no on-chain protection available. [5](#0-4)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L311-320)
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
