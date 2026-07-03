### Title
Missing Minimum Output Amount Check in L2 Pool `deposit` Functions Allows Users to Receive Fewer wrsETH Than Expected - (File: contracts/pools/RSETHPoolV2.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol)

---

### Summary

The `deposit` functions in `RSETHPoolV2`, `RSETHPoolV3`, and `RSETHPoolV2ExternalBridge` lack both a deadline parameter and a minimum output amount (`minAmountOut`) guard. A deposit transaction pending in the mempool will execute at whatever oracle rate is current at inclusion time. Because the rsETH/ETH rate monotonically increases as staking rewards accrue, a delayed transaction always yields fewer wrsETH than the user observed when they signed. The L1 counterpart (`LRTDepositPool`) correctly protects users with a `minRSETHAmountExpected` parameter; the L2 pool contracts do not.

---

### Finding Description

`RSETHPoolV2.deposit`, `RSETHPoolV3.deposit` (both ETH and token overloads), and `RSETHPoolV2ExternalBridge.deposit` all compute the wrsETH output at execution time using the live oracle rate:

```solidity
// RSETHPoolV2.sol L225-233
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle rate
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [1](#0-0) 

None of the three `deposit` entry points accept a `minAmountOut` or `deadline` argument:

```solidity
// RSETHPoolV2.sol L207
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
``` [2](#0-1) 

```solidity
// RSETHPoolV3.sol L246-265
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER) { ... }
// RSETHPoolV3.sol L271-293
function deposit(address token, uint256 amount, string memory referralId) external nonReentrant whenNotPaused onlySupportedToken(token) limitDailyMint(amount, token) { ... }
``` [3](#0-2) [4](#0-3) 

```solidity
// RSETHPoolV2ExternalBridge.sol L289
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
``` [5](#0-4) 

By contrast, the L1 deposit pool correctly enforces a caller-supplied minimum:

```solidity
// LRTDepositPool.sol L76-93
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
``` [6](#0-5) 

```solidity
// LRTDepositPool.sol L665-669
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [7](#0-6) 

The rsETH/ETH rate returned by `getRate()` increases over time as EigenLayer staking rewards accrue. A transaction submitted at rate R that executes at rate R+Δ silently mints fewer wrsETH with no on-chain protection.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but does not lose value.**

The deposited ETH is not lost; however, the user receives fewer wrsETH than the amount they observed when constructing the transaction. On L2 chains with variable block times or during periods of network congestion, the gap between submission and inclusion can be significant, and the rate drift is non-trivial over minutes to hours.

---

### Likelihood Explanation

**Medium.** The rsETH/ETH rate is designed to increase monotonically. Any mempool delay — however brief — produces a worse-than-expected output. On L2 networks (Arbitrum, Optimism, Base, etc.) where these pool contracts are deployed, sequencer delays or user-side gas underpricing can hold transactions for extended periods. No special attacker action is required; the loss is automatic and proportional to the delay.

---

### Recommendation

Add a `minAmountOut` parameter to all three `deposit` functions, mirroring the pattern already used in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minWrsETHExpected)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minWrsETHExpected) revert InsufficientOutputAmount();

    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

Optionally, add a `deadline` parameter and a `require(block.timestamp <= deadline)` guard for full Uniswap-style protection.

---

### Proof of Concept

1. User calls `RSETHPoolV2.deposit{value: 1 ether}("ref")` when `getRate()` returns `1.05e18` (1 ETH = ~0.952 wrsETH after fee).
2. Transaction sits in the mempool for 30 minutes due to gas underpricing.
3. The oracle is updated; `getRate()` now returns `1.06e18`.
4. Transaction executes: user receives `~0.943 wrsETH` instead of the `~0.952 wrsETH` they expected — a silent ~0.9% shortfall with no revert.
5. The user has no on-chain recourse because no `minAmountOut` check exists. [2](#0-1) [3](#0-2) [5](#0-4)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L207-219)
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

**File:** contracts/pools/RSETHPoolV2.sol (L225-233)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
