### Title
Missing Deadline and Minimum Output Protection in Pool Deposit Functions Allows Stale Transaction Execution - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
The `deposit()` functions in `RSETHPoolV3.sol` and `RSETHPoolV3ExternalBridge.sol` accept ETH or supported tokens and mint `wrsETH` based on a live oracle rate, but provide **no deadline parameter** and **no minimum output (slippage) parameter**. A user's pending transaction can sit in the mempool indefinitely and execute at a materially worse rate than intended, with no on-chain protection.

### Finding Description
Both ETH and token deposit paths in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` compute the `wrsETH` amount to mint entirely from the oracle rate at execution time:

```solidity
// RSETHPoolV3.sol – ETH deposit path
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    ...
}
``` [1](#0-0) 

```solidity
// RSETHPoolV3.sol – token deposit path
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused
    onlySupportedToken(token) limitDailyMint(amount, token)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    feeEarnedInToken[token] += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    ...
}
``` [2](#0-1) 

The rate is fetched from the oracle at execution time:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [3](#0-2) 

Neither function accepts a `deadline` nor a `minRsETHAmountExpected` parameter. The identical pattern exists in `RSETHPoolV3ExternalBridge.sol`: [4](#0-3) 

By contrast, the L1 `LRTDepositPool.depositETH()` does accept a `minRSETHAmountExpected` slippage guard (though still no deadline): [5](#0-4) 

### Impact Explanation
rsETH is an LRT whose exchange rate monotonically increases over time as Ethereum staking rewards accrue. A deposit transaction that lingers in the mempool (due to low gas, network congestion, or deliberate delay) will execute at a higher `rsETHToETHrate`, meaning the depositor receives **fewer `wrsETH` tokens** than they expected when they signed the transaction. Because there is no `minRsETHAmountExpected` guard and no deadline, the contract cannot revert on the user's behalf. The user's ETH is consumed but they receive a smaller token allocation than intended.

**Impact: Low** — Contract fails to deliver the promised token quantity to the user, but the user's ETH value is not directly stolen (each `wrsETH` token is worth proportionally more). Matches the allowed scope: *"Contract fails to deliver promised returns, but doesn't lose value."*

### Likelihood Explanation
Likelihood is **Low-to-Medium**. On L2 networks (where these pool contracts are deployed), gas fees are generally low and transactions are rarely stuck for extended periods. However, during L2 sequencer outages, network congestion events, or when a user deliberately submits with a very low fee, transactions can remain pending for hours or days. The rsETH rate increases continuously, so any meaningful delay translates directly into fewer tokens minted.

### Recommendation
1. Add a `uint256 deadline` parameter to both `deposit()` overloads and revert if `block.timestamp > deadline`.
2. Add a `uint256 minRsETHAmountExpected` parameter and revert if the computed `rsETHAmount < minRsETHAmountExpected`, mirroring the protection already present in `LRTDepositPool.depositETH()`.

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount, uint256 deadline)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    if (block.timestamp > deadline) revert DeadlineExpired();
    ...
    if (rsETHAmount < minRsETHAmount) revert InsufficientOutputAmount();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

### Proof of Concept
1. Alice submits a `deposit{value: 10 ether}("ref")` call to `RSETHPoolV3` on an L2 with a low gas price. At submission time, `rsETHToETHrate = 1.05e18`, so she expects `≈9.52 wrsETH` (after fee).
2. The transaction sits in the mempool for several days due to low gas.
3. The oracle rate updates to `1.06e18` (staking rewards accrued).
4. The transaction is included. Alice receives `≈9.43 wrsETH` — roughly 0.9% fewer tokens than expected.
5. Because there is no `minRsETHAmountExpected` check and no deadline, the contract cannot revert, and Alice has no recourse.

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
