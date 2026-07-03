### Title
L2 Pool Depositors Cannot Specify Minimum wrsETH Received, Causing Potential Loss of Funds - (`contracts/pools/RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3.sol`, `RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

All L2 RSETHPool `deposit()` variants lack a `minRsETHAmount` parameter. The wrsETH/rsETH amount minted to the depositor is computed at execution time from a live oracle rate. If the rate moves between when a user previews the swap and when their transaction executes, they receive fewer wrsETH tokens than expected with no on-chain protection.

---

### Finding Description

Every L2 pool `deposit()` function computes the output amount using `viewSwapRsETHAmountAndFee`, which reads `getRate()` from a live oracle at execution time:

```solidity
// RSETHPoolV3ExternalBridge.sol
function deposit(string memory referralId) external payable ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minimum check
}
``` [1](#0-0) 

The rate calculation:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [2](#0-1) 

The same pattern is present in every pool variant: [3](#0-2) [4](#0-3) [5](#0-4) 

By contrast, the L1 `LRTDepositPool` correctly accepts a `minRSETHAmountExpected` parameter and enforces it before minting: [6](#0-5) 

The L2 pools provide a `getMinAmount()` helper and a `viewSwapRsETHAmountAndFee()` view function for off-chain previews, but neither is enforced inside `deposit()`. The gap between preview and execution is exploitable by normal mempool ordering. [7](#0-6) 

---

### Impact Explanation

A depositor who previews `viewSwapRsETHAmountAndFee` off-chain and then submits a `deposit()` transaction can receive materially fewer wrsETH tokens than expected if the oracle rate increases (rsETH appreciates in ETH terms) between preview and execution. Because wrsETH represents a proportional claim on the underlying restaked ETH, receiving fewer shares is a direct, permanent loss of yield-bearing value. The loss magnitude scales with deposit size and rate movement. This matches the **Low** impact class: "Contract fails to deliver promised returns, but doesn't lose value" — or **Medium** if the rate movement is large enough to constitute a temporary freeze of the shortfall in yield.

---

### Likelihood Explanation

The rsETH oracle rate is updated by the protocol whenever staking rewards are distributed or assets are rebalanced. Any deposit transaction that sits in the mempool during an oracle update will silently receive fewer wrsETH than previewed. This is a routine, non-adversarial condition on every L2 chain where these pools are deployed (Arbitrum, Optimism, Base, etc.). No special attacker capability is required — ordinary network congestion or a competing oracle update transaction is sufficient.

---

### Recommendation

Add a `minRsETHAmount` parameter to every `deposit()` overload in all L2 pool contracts, mirroring the pattern already used in `LRTDepositPool`:

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

Apply the same change to the token `deposit(address, uint256, string)` overloads across `RSETHPool`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolNoWrapper`.

---

### Proof of Concept

1. Alice calls `viewSwapRsETHAmountAndFee(1 ETH)` on `RSETHPoolV3ExternalBridge`. The oracle returns `rsETHToETHrate = 1.05e18`, so she expects `~0.952 wrsETH`.
2. Alice submits `deposit{value: 1 ether}("ref")`.
3. Before Alice's tx is mined, the Kelp oracle is updated: staking rewards push `rsETHToETHrate` to `1.10e18`.
4. Alice's tx executes. `viewSwapRsETHAmountAndFee` now returns `~0.909 wrsETH`.
5. Alice receives `~0.909 wrsETH` instead of `~0.952 wrsETH` — a ~4.5% shortfall with no revert and no recourse.
6. Because wrsETH accrues staking yield, this shortfall compounds over time. [8](#0-7)

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L540-544)
```text
    function getMinAmount(uint256 amount, uint256 slippageTolerance) external pure returns (uint256) {
        if (slippageTolerance > 10_000) revert InvalidSlippageTolerance();

        return amount - (amount * slippageTolerance / 10_000);
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

**File:** contracts/pools/RSETHPool.sol (L271-305)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

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

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
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
