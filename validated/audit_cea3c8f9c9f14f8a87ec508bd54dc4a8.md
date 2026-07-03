### Title
Missing Slippage Protection in `deposit` Functions Allows Users to Receive Fewer wrsETH Than Expected - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
All `deposit` functions across the L2 pool contracts (`RSETHPoolV3`, `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2ExternalBridge`) compute the wrsETH/rsETH output amount solely from a cross-chain oracle rate (`getRate()`) with no caller-supplied minimum output guard. If the oracle rate is updated in the same block — or in any block between when the user previews the rate and when their transaction is mined — the user silently receives fewer wrsETH than they expected, with no on-chain recourse.

### Finding Description
Every public `deposit` entry point follows the same pattern:

```solidity
// RSETHPoolV3.sol – ETH deposit (line 246-265)
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);          // ← no minRsETHAmount check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The output amount is derived from `getRate()`:

```solidity
// RSETHPoolV3.sol – rate-based calculation (line 299-308)
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();            // ← live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

`getRate()` reads from a `CrossChainRateReceiver` that is updated by the protocol's `MultiChainRateProvider` via LayerZero. The rate is pushed on-chain by the protocol and can be updated at any time. There is no `minRsETHAmount` parameter in any of the deposit overloads across any pool variant:

- `RSETHPool.sol` `deposit(string)` / `deposit(address,uint256,string)` — lines 265–305
- `RSETHPoolNoWrapper.sol` `deposit(string)` / `deposit(address,uint256,string)` — lines 231–271
- `RSETHPoolV3.sol` `deposit(string)` / `deposit(address,uint256,string)` — lines 246–293
- `RSETHPoolV3WithNativeChainBridge.sol` `deposit(string)` / `deposit(address,uint256,string)` — lines 282–329
- `RSETHPoolV2ExternalBridge.sol` `deposit(string)` — lines 289–301

### Impact Explanation
A user calls `viewSwapRsETHAmountAndFee` off-chain to preview the expected wrsETH output, then submits a `deposit` transaction. If the oracle rate is updated (rate increases → rsETH is more expensive in ETH terms) before the user's transaction is included, the user receives fewer wrsETH than previewed. Because there is no `minRsETHAmount` guard, the transaction succeeds silently and the user has no on-chain protection. The user's ETH is consumed but they receive a smaller wrsETH balance than the protocol's own view function indicated at submission time.

**Impact: Low — Contract fails to deliver promised returns, but does not lose the deposited value in absolute ETH terms.**

### Likelihood Explanation
The oracle rate is updated by the protocol's cross-chain infrastructure whenever the L1 rsETH/ETH rate changes. On active networks this can happen multiple times per day. Any deposit transaction that is pending in the mempool when a rate update is mined will execute at the new, less favorable rate. This is a routine, non-adversarial scenario that affects every depositor who previews the rate before submitting.

### Recommendation
Add a `minRsETHAmount` parameter to every `deposit` overload and revert if the computed output falls below it:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
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

The front-end should compute `minRsETHAmount` from the current oracle rate with an acceptable tolerance (e.g. 0.1 %) before submitting the transaction.

### Proof of Concept

1. Oracle rate is `1.05e18` (1 ETH = ~0.952 wrsETH). User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees they will receive `≈0.952 wrsETH`.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is mined, the protocol's `MultiChainRateProvider` pushes a new rate update via LayerZero; `CrossChainRateReceiver` updates the pool oracle to `1.10e18`.
4. User's `deposit` transaction executes. `getRate()` now returns `1.10e18`, so `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909 wrsETH`.
5. User receives `0.909 wrsETH` instead of the previewed `0.952 wrsETH` — a ~4.5 % shortfall — with no revert and no recourse.

The root cause is confirmed at: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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
