### Title
Missing Minimum Output Amount (Slippage) Check in Pool `deposit` Functions - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
All RSETH pool `deposit` functions accept ETH or supported tokens from users and return wrsETH/rsETH based on the current oracle rate, but none accept a `minRsETHAmount` parameter. If the oracle rate updates between when a user previews the swap and when the transaction executes, the user receives fewer rsETH tokens than expected with no on-chain protection.

### Finding Description
Every pool variant exposes public `deposit` functions callable by any user. The amount of rsETH minted is computed at execution time using `viewSwapRsETHAmountAndFee`, which reads the live oracle rate via `getRate()`. No minimum output guard is provided.

In `RSETHPoolV3ExternalBridge.sol`:

```solidity
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);  // oracle-dependent
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);                                     // no minRsETHAmount check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
``` [1](#0-0) 

The same pattern is present in the token-deposit overload: [2](#0-1) 

And identically across every other pool contract: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

The rate computation reads the oracle at execution time:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [8](#0-7) 

### Impact Explanation
**Low.** If the rsETH/ETH oracle rate increases between the user's off-chain preview and on-chain execution (e.g., due to a scheduled oracle update), the user receives fewer rsETH tokens than they were shown. The ETH value of the received rsETH is approximately preserved (rsETH is worth more per token), so no direct ETH loss occurs. However, the contract fails to deliver the promised token quantity, which can break downstream assumptions (e.g., a user who needed a specific rsETH amount for a DeFi position). This maps to: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
Medium-low. The rsETH/ETH oracle rate is monotonically increasing (staking rewards accrue), so every oracle update increases the rate and reduces the rsETH output for a given ETH input. Oracle updates are periodic and predictable, but a user whose transaction is delayed in the mempool (e.g., during network congestion) will silently receive fewer tokens than previewed. No adversarial action is required — normal oracle operation is sufficient.

### Recommendation
Add a `minRsETHAmount` parameter to each `deposit` function and revert if the minted amount falls below it:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert SlippageExceeded();

    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

Apply the same pattern to all token-deposit overloads across `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV2`, `RSETHPoolV2NBA`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`.

### Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees they will receive `X` rsETH at the current oracle rate `R`.
2. User submits `deposit{value: 1 ether}(referralId)`.
3. Before the transaction is mined, the rsETH oracle updates: rate increases from `R` to `R'` (where `R' > R`).
4. Transaction executes: `rsETHAmount = 1e18 * amountAfterFee / R'` — fewer tokens than `X`.
5. User receives `X' < X` rsETH with no revert, no warning, and no recourse.

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-428)
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
