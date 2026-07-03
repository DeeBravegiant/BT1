### Title
Missing Minimum Output Amount (Slippage Protection) in L2 Pool `deposit()` Functions - (`contracts/pools/RSETHPool.sol`, `RSETHPoolV2.sol`, `RSETHPoolV3.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

All L2 pool `deposit()` functions lack a `minRSETHAmountExpected` (minimum output) parameter. A user previews the swap via `viewSwapRsETHAmountAndFee()`, observes a favorable oracle rate, submits `deposit()`, but by the time the transaction executes the oracle rate has changed. The user receives fewer wrsETH/rsETH than expected with no on-chain protection. This is the direct analog of the `placeBid()` issue: a user's transaction is based on state observed off-chain, that state changes before execution, and the contract provides no mechanism to revert if the outcome is worse than expected.

---

### Finding Description

Every L2 pool variant exposes a two-step UX: call `viewSwapRsETHAmountAndFee()` to preview the output, then call `deposit()` to execute. The preview and the execution both read the live oracle rate, but there is no parameter that lets the user commit to a minimum acceptable output.

`RSETHPool.deposit()`: [1](#0-0) 

`viewSwapRsETHAmountAndFee()` (ETH path) — the rate read here at preview time is not guaranteed at execution time: [2](#0-1) 

The same pattern is present in every other pool variant: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

By contrast, the L1 `LRTDepositPool` already enforces a `minRSETHAmountExpected` guard in `_beforeDeposit()`: [9](#0-8) 

The L2 pools are missing this protection entirely.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previewed a rate of `R₀` and submitted `deposit()` may execute against a higher rate `R₁ > R₀` (rsETH has appreciated). They receive `amount * 1e18 / R₁` wrsETH instead of the expected `amount * 1e18 / R₀`. Their deposited ETH is not lost — it is held by the pool and they hold wrsETH worth the same ETH at the new rate — but they receive fewer tokens than the UI promised, with no ability to revert the transaction if the shortfall is unacceptable.

---

### Likelihood Explanation

**Medium.** The rsETH oracle rate (`getRate()`) increases continuously as EigenLayer staking rewards accrue. On L2 networks, transactions can sit in the mempool for multiple blocks. Any delay between the user's preview call and transaction inclusion results in a worse-than-expected output. No adversarial action is required; normal protocol operation is sufficient to trigger the discrepancy.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to every `deposit()` overload in all L2 pool contracts, mirroring the existing guard in `LRTDepositPool`:

```solidity
// Before (RSETHPool, RSETHPoolV2, RSETHPoolV3, RSETHPoolNoWrapper, etc.)
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    // no minimum check
    ...
}

// After
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply the same fix to the token-deposit overloads (`deposit(address token, uint256 amount, string memory referralId)`).

---

### Proof of Concept

1. Alice calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPool`. Oracle rate is `1.05e18` (1 rsETH = 1.05 ETH). She expects to receive `~0.952 wrsETH`.
2. Alice submits `deposit("ref")` with `msg.value = 1 ether`. The transaction sits in the mempool.
3. Before Alice's transaction is mined, staking rewards are distributed and the oracle rate updates to `1.06e18`.
4. Alice's transaction executes. `viewSwapRsETHAmountAndFee` is called again inside `deposit()` with the new rate:
   - `rsETHAmount = 1e18 * 1e18 / 1.06e18 ≈ 0.943 wrsETH`
5. Alice receives `~0.943 wrsETH` instead of the `~0.952 wrsETH` she saw in the UI — a shortfall of ~0.009 wrsETH (~0.95% of her deposit) with no revert possible. [2](#0-1)

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

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
