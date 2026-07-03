### Title
Missing Minimum Output Amount (Slippage Protection) in L2 Pool `deposit` Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol, contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

All publicly callable `deposit` functions across the L2 pool contracts lack a `minRsETHAmount` (minimum output) parameter. Users who deposit ETH or supported tokens to receive `wrsETH`/`rsETH` have no on-chain slippage protection. If the oracle rate changes between transaction submission and execution, users silently receive fewer tokens than expected with no recourse.

---

### Finding Description

The L1 `LRTDepositPool` correctly implements slippage protection via a `minRSETHAmountExpected` parameter in both `depositETH` and `depositAsset`, enforced in `_beforeDeposit`:

```solidity
// LRTDepositPool.sol
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...) external ...
``` [1](#0-0) [2](#0-1) [3](#0-2) 

In contrast, every L2 pool `deposit` function omits this parameter entirely. For example, in `RSETHPoolV3.sol`:

```solidity
// RSETHPoolV3.sol — no minRsETHAmount parameter
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER) {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    wrsETH.mint(msg.sender, rsETHAmount);
}

function deposit(address token, uint256 amount, string memory referralId) external nonReentrant whenNotPaused ... {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    wrsETH.mint(msg.sender, rsETHAmount);
}
``` [4](#0-3) [5](#0-4) 

The minted amount is computed entirely from the oracle rate at execution time:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [6](#0-5) 

The same pattern is present in all other L2 pool variants: [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) 

---

### Impact Explanation

If the `rsETHOracle` rate increases (rsETH appreciates in ETH terms) between when a user simulates the transaction off-chain and when it is mined, the user receives fewer `wrsETH` tokens than expected. The user's ETH/token is accepted by the pool but the minted output is silently reduced. There is no on-chain check to revert the transaction if the output falls below a user-acceptable threshold.

**Impact: Low** — Contract fails to deliver promised returns, but does not lose value (the ETH remains in the pool; the user simply receives fewer wrsETH than anticipated).

---

### Likelihood Explanation

The rsETH oracle rate is updated periodically from L1 via the rate propagation system. Any rate update that lands in the same block as, or just before, a user's deposit transaction will silently reduce the user's output. This is a routine occurrence on any active L2 deployment and requires no attacker — it is a structural property of the missing parameter. Any unprivileged depositor is affected.

---

### Recommendation

Add a `minRsETHAmountExpected` parameter to all L2 pool `deposit` functions, mirroring the L1 `LRTDepositPool` pattern:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

Apply the same fix to the token-deposit overload and to all pool variants (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2ExternalBridge`, `RSETHPoolNoWrapper`).

---

### Proof of Concept

1. Oracle rate is currently `1.05e18` (1 rsETH = 1.05 ETH).
2. User simulates `deposit{value: 1 ether}("ref")` off-chain and expects `≈0.952 wrsETH`.
3. Before the transaction is mined, the oracle rate is updated to `1.10e18`.
4. `viewSwapRsETHAmountAndFee(1 ether)` now returns `≈0.909 wrsETH` (after fee).
5. The contract mints `0.909 wrsETH` to the user with no revert — the user receives ~4.5% fewer tokens than expected and has no on-chain protection to prevent this. [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-87)
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
```

**File:** contracts/LRTDepositPool.sol (L99-117)
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
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
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

**File:** contracts/pools/RSETHPoolV3.sol (L299-310)
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-270)
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
```
