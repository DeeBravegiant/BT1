### Title
No Deadline Protection on Deposit Transactions Allows Execution at Unfavorable Oracle Rates - (File: contracts/pools/RSETHPoolV2.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

All five pool contracts expose public `deposit()` functions that mint wrsETH to users based on the current oracle rate at execution time. None of these functions accept a deadline parameter or a minimum output amount. A pending deposit transaction can therefore be executed at any future block — including one where the rsETH/ETH oracle rate has risen — causing the user to receive fewer wrsETH tokens than they anticipated when they submitted the transaction.

---

### Finding Description

The `deposit()` functions across all pool variants accept ETH or a supported ERC-20 token and mint wrsETH according to the live oracle rate:

```solidity
// RSETHPoolV2.sol L207-L218
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The minted amount is computed entirely from the oracle rate at the moment of inclusion:

```solidity
// RSETHPoolV2.sol L225-L234
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The same pattern is present in every pool variant: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

There is no `deadline` argument and no `minAmountOut` guard anywhere in these call paths. The absence of a deadline is functionally equivalent to the anti-pattern described in the reference report (setting `deadline = block.timestamp`): the transaction is valid for inclusion at any future block.

---

### Impact Explanation

rsETH is a yield-bearing token whose ETH value (`rsETHToETHrate`) monotonically increases over time as staking rewards accrue. A deposit transaction that sits in the mempool during a period of network congestion and is eventually included in a later block will execute against a higher oracle rate, minting fewer wrsETH tokens than the user observed when they signed the transaction. The user receives the correct current-market amount but less than the amount they intended to accept — a failure to deliver the promised return without an outright loss of principal.

**Impact:** Low — Contract fails to deliver promised returns, but doesn't lose value.

---

### Likelihood Explanation

Any user depositing ETH or a supported token through any of the five pool contracts is exposed. Network congestion events (e.g., high-gas periods, L2 sequencer delays) are common and can delay transaction inclusion by minutes to hours. Because the rsETH oracle rate updates continuously, even a short delay can produce a measurable shortfall in minted wrsETH. No special attacker capability is required; the condition arises from ordinary mempool dynamics.

---

### Recommendation

Add a `deadline` parameter to each `deposit()` function and revert if `block.timestamp > deadline`. Additionally, add a `minAmountOut` parameter and revert if the computed `rsETHAmount < minAmountOut`. This gives depositors full control over the time window and the minimum acceptable output, mirroring the standard pattern used by Uniswap, Curve, and other AMMs.

```solidity
function deposit(
    string memory referralId,
    uint256 minAmountOut,
    uint256 deadline
) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
    if (block.timestamp > deadline) revert DeadlineExpired();
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minAmountOut) revert InsufficientOutput();
    ...
}
```

---

### Proof of Concept

1. Alice calls `RSETHPoolV2.deposit{value: 1 ether}("ref")` when the oracle rate is `1.05 ether` per rsETH, expecting to receive `≈ 0.952 wrsETH`.
2. The transaction is broadcast but not immediately included due to network congestion.
3. The rsETH oracle rate updates to `1.06 ether` per rsETH before Alice's transaction is mined.
4. Alice's transaction is included; `viewSwapRsETHAmountAndFee(1 ether)` now returns `≈ 0.943 wrsETH`.
5. Alice receives `≈ 0.009 wrsETH` less than she expected with no ability to prevent this outcome, because there is no deadline or minimum-output check in the function. [2](#0-1) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L207-218)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
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
