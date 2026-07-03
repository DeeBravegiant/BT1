### Title
No User-Controlled Minimum Output in L2 Pool Deposit Functions Exposes Depositors to Unfavorable On-Chain Rate Execution - (File: contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
All L2 pool `deposit()` functions compute the rsETH output amount entirely on-chain from the current oracle rate, but accept no user-controlled minimum output parameter. This is the structural analog to the external report: on-chain rate-based output calculation with no user-controlled slippage floor, leaving depositors exposed to receiving fewer tokens than expected whenever the oracle rate moves between submission and execution.

### Finding Description
Every L2 pool deposit path follows the same pattern. In `RSETHPool`:

```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    if (!isEthDepositEnabled) revert EthDepositDisabled();
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
``` [1](#0-0) 

The rsETH amount is computed by `viewSwapRsETHAmountAndFee`, which reads the exchange rate from `rsETHOracle` at execution time:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live on-chain oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [2](#0-1) 

The identical pattern exists in `RSETHPoolNoWrapper.deposit()`, `RSETHPoolV2ExternalBridge.deposit()`, and `RSETHPoolV3ExternalBridge.deposit()`: [3](#0-2) [4](#0-3) [5](#0-4) 

None of these functions accept a `minRsETHAmount` parameter. The user has zero ability to bound the minimum output they will receive.

This is in direct contrast to the main `LRTDepositPool.depositETH` and `LRTDepositPool.depositAsset`, which both accept and enforce a `minRSETHAmountExpected` parameter:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
``` [6](#0-5) 

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [7](#0-6) 

The protocol therefore already recognises the need for user-controlled slippage protection in the L1 deposit path but has not applied it to any of the L2 pool deposit paths.

The oracle rate is sourced from `rsETHOracle.getRate()`: [8](#0-7) 

The underlying `LRTOracle.rsETHPrice` is a stored value updated by the public `updateRSETHPrice()` function, meaning any actor can trigger a price update immediately before a pending deposit transaction is mined, causing the depositor to receive a different rsETH amount than they observed when constructing the transaction: [9](#0-8) 

### Impact Explanation
A depositor submits a transaction after observing a favourable oracle rate. Before the transaction is included, `updateRSETHPrice()` is called (by anyone — it is public), updating the stored rate. The depositor's transaction executes at the new, less favourable rate and receives fewer rsETH tokens than expected. The depositor's ETH is consumed and cannot be recovered; the shortfall in rsETH is permanent. This constitutes a failure to deliver the promised return on deposit.

**Impact: Low — Contract fails to deliver promised returns, but does not lose the deposited value.**

### Likelihood Explanation
`updateRSETHPrice()` is callable by any address with no access restriction. Any depositor using these pool contracts on any supported L2 is exposed on every deposit. The risk is elevated during periods of active oracle updates (e.g., after EigenLayer reward accrual) and is reachable by any unprivileged depositor without any special setup.

### Recommendation
Add a `minRsETHAmount` parameter to all L2 pool `deposit()` functions, mirroring the pattern already used in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert InsufficientOutputAmount();
    ...
}
```

This gives depositors a trustless guarantee on the minimum rsETH they will receive, regardless of oracle updates that occur between transaction submission and execution.

### Proof of Concept
1. Depositor observes `rsETHOracle.getRate()` = R and submits `deposit{value: 1 ether}("")` expecting ≈ `1e18 / R` rsETH.
2. Attacker (or anyone) calls `LRTOracle.updateRSETHPrice()`, which increases `rsETHPrice` (e.g., due to accrued rewards), raising R to R′ > R.
3. Depositor's transaction is mined; `viewSwapRsETHAmountAndFee` now uses R′, yielding `1e18 / R′ < 1e18 / R` rsETH.
4. Depositor receives fewer rsETH tokens than expected with no recourse, as no minimum output check exists.

### Citations

**File:** contracts/pools/RSETHPool.sol (L254-256)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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

**File:** contracts/LRTDepositPool.sol (L666-669)
```text

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```
