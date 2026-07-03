### Title
Missing Deadline and Minimum Amount Protection in Pool `deposit()` Functions - (`contracts/pools/RSETHPool.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

Every L2 pool `deposit()` function that swaps ETH or a supported token for rsETH/wrsETH accepts no `deadline` parameter and no `minRsETHAmountOut` parameter. A validator can hold the transaction in the mempool until the oracle rate moves unfavorably, and the user has no on-chain protection against receiving fewer rsETH tokens than they expected.

---

### Finding Description

All pool deposit entry points follow the same pattern:

```solidity
// RSETHPool.sol – ETH path (identical pattern in all pool variants)
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

`viewSwapRsETHAmountAndFee` reads the live oracle rate at execution time:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle call
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

Because rsETH is a yield-bearing token, its ETH-denominated rate (`rsETHToETHrate`) increases monotonically over time. A transaction submitted when the rate is `R` but mined when the rate is `R + Δ` produces `amountAfterFee * 1e18 / (R + Δ)` rsETH — strictly fewer tokens than the user anticipated. There is no `deadline` to bound how long the transaction can sit in the mempool, and no `minRsETHAmountOut` to reject execution if the received amount falls below the user's acceptable threshold.

By contrast, `LRTDepositPool.depositETH()` — the L1 equivalent — explicitly accepts a `minRSETHAmountExpected` parameter and reverts if the minted amount is below it, demonstrating that the protocol is aware of this protection and intentionally implemented it on L1 but omitted it from every L2 pool variant.

Affected functions (all share the same root cause):
- `RSETHPool.deposit(string)` and `RSETHPool.deposit(address,uint256,string)`
- `RSETHPoolV2ExternalBridge.deposit(string)`
- `RSETHPoolV3ExternalBridge.deposit(string)` and `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)`
- `RSETHPoolV3.deposit(string)` and `RSETHPoolV3.deposit(address,uint256,string)`
- `RSETHPoolNoWrapper.deposit(string)` and `RSETHPoolNoWrapper.deposit(address,uint256,string)`
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` and `RSETHPoolV3WithNativeChainBridge.deposit(address,uint256,string)`

---

### Impact Explanation

A user who submits a deposit transaction expecting `X` rsETH tokens receives `X - ε` tokens if the transaction is delayed and the oracle rate ticks upward. The user's ETH is fully consumed but they receive fewer rsETH tokens than they agreed to accept, meaning they earn less future yield and hold a smaller share of the underlying basket. This matches **"Low — Contract fails to deliver promised returns, but doesn't lose value"** from the allowed impact scope.

---

### Likelihood Explanation

rsETH's oracle rate increases continuously as staking rewards accrue. Any network congestion, gas price spike, or deliberate validator withholding causes pending transactions to age, and every second of aging increases the rate and decreases the rsETH output. This is a routine, low-effort scenario requiring no special attacker capability — it can occur passively during normal network conditions.

---

### Recommendation

1. Add a `uint256 minRsETHAmountOut` parameter to every pool `deposit()` overload and revert if `rsETHAmount < minRsETHAmountOut`.
2. Optionally add a `uint256 deadline` parameter and revert if `block.timestamp > deadline`.

```diff
- function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
+ function deposit(string memory referralId, uint256 minRsETHAmountOut, uint256 deadline)
+     external payable nonReentrant whenNotPaused {
+     if (block.timestamp > deadline) revert DeadlineExpired();
      ...
      (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
+     if (rsETHAmount < minRsETHAmountOut) revert InsufficientOutputAmount();
      ...
  }
```

The same change should be applied to the token-path overload and to all pool variants listed above.

---

### Proof of Concept

1. Alice calls `RSETHPool.deposit{value: 1 ether}("ref")` when the oracle rate is `1.05e18` (1 rsETH = 1.05 ETH). She expects `~0.952 rsETH`.
2. The transaction sits in the mempool for several hours due to gas price competition.
3. Staking rewards accrue; the oracle rate updates to `1.06e18`.
4. The transaction is mined. `viewSwapRsETHAmountAndFee` reads `1.06e18` and returns `~0.943 rsETH`.
5. Alice receives `~0.009 rsETH` fewer than she expected, with no on-chain mechanism to prevent this outcome.
6. Because there is no `deadline` and no `minRsETHAmountOut`, the transaction succeeds silently. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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
