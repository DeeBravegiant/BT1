### Title
No Minimum rsETH Output Check in L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPool.sol)

### Summary
All L2 deposit pool contracts (`RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) expose public `deposit()` functions that compute the rsETH/wrsETH output amount at execution time using the oracle rate, but accept no `minRSETHAmountExpected` parameter. The L1 `LRTDepositPool.depositAsset()` explicitly includes this guard; the L2 equivalents do not.

### Finding Description
Every L2 pool `deposit()` function computes the minted rsETH amount as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // ETH path
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate; // LST path
```

where `rsETHToETHrate = getRate()` is read from the oracle at the moment of execution. No lower bound on `rsETHAmount` is enforced before the mint/transfer.

The L1 counterpart explicitly protects users:

```solidity
function depositAsset(
    address asset,
    uint256 depositAmount,
    uint256 minRSETHAmountExpected,   // ← slippage guard
    string calldata referralId
) external { ... }
```

On L2, the oracle rate is propagated via cross-chain messages (LayerZero / CCIP). A rate update can land in the mempool and be included in the same block as, or immediately before, a user's `deposit()` transaction. Because the rsETH/ETH rate is monotonically increasing (staking rewards), any oracle refresh between tx submission and execution reduces the rsETH output the user receives, with no on-chain mechanism for the user to reject the execution.

Affected entry points (all publicly callable, no role restriction):
- `RSETHPool.deposit(string)` and `RSETHPool.deposit(address,uint256,string)`
- `RSETHPoolNoWrapper.deposit(string)` and `RSETHPoolNoWrapper.deposit(address,uint256,string)`
- `RSETHPoolV3.deposit(string)` and `RSETHPoolV3.deposit(address,uint256,string)`
- `RSETHPoolV3ExternalBridge.deposit(string)` and `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)`
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` and `RSETHPoolV3WithNativeChainBridge.deposit(address,uint256,string)`

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previews the expected rsETH output off-chain (via `viewSwapRsETHAmountAndFee`) and submits a transaction may receive materially fewer rsETH/wrsETH tokens than anticipated if the oracle rate is updated before their transaction executes. Because rsETH is a yield-bearing token whose rate only increases, the user's ETH-denominated value is preserved, but they receive fewer receipt tokens than they intended. This can matter when a user is targeting a specific rsETH balance (e.g., to meet a collateral threshold in a lending protocol).

### Likelihood Explanation
The rsETH oracle rate is updated regularly via cross-chain rate propagation. On active L2 chains (Arbitrum, Base, Optimism, etc.) with frequent oracle refreshes, the window for a rate update to land between a user's off-chain preview and on-chain execution is non-trivial. No attacker action is required; normal protocol operation is sufficient to trigger the discrepancy.

### Recommendation
Add a `minRSETHAmountExpected` parameter to all L2 pool `deposit()` functions, mirroring the existing guard in `LRTDepositPool.depositAsset()`:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert SlippageExceeded();
    ...
}
```

Apply the same pattern to the token-deposit overloads and to all pool variants.

### Proof of Concept

1. At block N, `getRate()` returns `1.05e18` (1 rsETH = 1.05 ETH). User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees they will receive `~0.952 wrsETH`.
2. User submits `RSETHPool.deposit{value: 1 ether}("ref")`.
3. Before the tx is included, a cross-chain rate update arrives and sets `rsETHToETHrate = 1.10e18`.
4. User's tx executes at block N+1. `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909 wrsETH` — roughly 4.5% fewer tokens than previewed.
5. The user has no recourse; the transaction succeeded and their ETH is gone.

The L1 path would have reverted at step 4 if `minRSETHAmountExpected = 0.95e18` had been supplied. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/pools/RSETHPool.sol (L311-347)
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
    /// @param amount The amount of token
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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
