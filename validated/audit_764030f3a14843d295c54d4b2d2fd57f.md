Audit Report

## Title
Missing Minimum Output Protection in L2 Pool `deposit()` Functions - (`contracts/pools/RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

## Summary
All L2 pool `deposit()` entry points accept ETH or supported tokens and compute the output rsETH/wrsETH amount from a live oracle rate at execution time, but accept no `minRsETHAmountExpected` parameter and perform no minimum-output check. If the oracle rate increases between transaction submission and inclusion, the user silently receives fewer tokens than observed off-chain with no on-chain recourse. The L1 `LRTDepositPool` already enforces this protection, confirming the L2 omission is unintentional.

## Finding Description
Every L2 pool `deposit()` function follows the same pattern: read the live rate via `viewSwapRsETHAmountAndFee()` (which internally calls `getRate()`), compute `rsETHAmount`, and transfer/mint that amount to `msg.sender` — with no floor check.

`RSETHPool.sol` ETH deposit (L265–278): no `minRsETHAmountExpected` parameter, no floor check before `safeTransfer`. [1](#0-0) 

`RSETHPool.sol` token deposit (L284–305): same pattern for ERC-20 deposits. [2](#0-1) 

`RSETHPoolNoWrapper.sol` ETH deposit (L231–244): identical omission. [3](#0-2) 

`RSETHPoolV2ExternalBridge.sol` ETH deposit (L289–301): identical omission. [4](#0-3) 

`RSETHPoolV3.sol` ETH deposit (L246–265): identical omission. [5](#0-4) 

`RSETHPoolV3ExternalBridge.sol` ETH deposit (L366–384): identical omission. [6](#0-5) 

`RSETHPoolV3WithNativeChainBridge.sol` ETH deposit (L282–301): identical omission. [7](#0-6) 

The rate used is fetched at execution time inside `viewSwapRsETHAmountAndFee`: [8](#0-7) 

By contrast, `LRTDepositPool._beforeDeposit()` explicitly reverts if the computed mint amount falls below the caller-supplied minimum: [9](#0-8) 

The root cause is the absence of a caller-supplied floor parameter and a corresponding revert in all L2 pool deposit paths. No existing guard (zero-amount check, daily mint cap, pause) compensates for this.

## Impact Explanation
When the oracle rate increases between a user's transaction submission and its on-chain execution, `rsETHAmount` is computed at the higher rate, yielding fewer tokens than the user observed off-chain. The transaction does not revert; the user's ETH/tokens are consumed and they receive a smaller-than-expected rsETH position. The underlying ETH value of the received shares is correct at the new rate, so no funds are lost outright — this maps exactly to the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value."**

## Likelihood Explanation
The rsETH oracle rate is updated by the protocol whenever EigenLayer positions accrue rewards or the LST basket changes — routine, recurring events. Any deposit transaction pending in the mempool during an oracle update window is silently affected. No attacker action is required; the exposure is structural to normal protocol operation. Likelihood is **Medium**.

## Recommendation
Add a `uint256 minRsETHAmountExpected` parameter to every L2 pool `deposit()` function and revert if the computed output falls below it, mirroring the L1 pattern:

```solidity
if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
```

This should be applied to all ETH and token deposit entry points across `RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, and `RSETHPoolV3WithNativeChainBridge.sol`.

## Proof of Concept
1. User queries `RSETHPoolV3.viewSwapRsETHAmountAndFee(1 ether)` off-chain; oracle rate is `1.05e18`, expected output ≈ 0.952 wrsETH.
2. User submits `RSETHPoolV3.deposit{value: 1 ether}("ref")`.
3. Before the transaction is mined, the protocol updates the oracle to `1.10e18`.
4. Transaction executes: `rsETHAmount = (1e18 - fee) * 1e18 / 1.10e18` ≈ 0.909 wrsETH.
5. No revert occurs; user receives ~4.5% fewer tokens than anticipated with no recourse.

**Foundry fork test plan:**
```solidity
function testDepositSlippage() public {
    // Fork L2 at block N where rate = 1.05e18
    uint256 before = wrsETH.balanceOf(user);
    // Simulate oracle update to 1.10e18 (prank oracle updater)
    oracle.setRate(1.10e18);
    // User deposit executes at new rate
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref");
    uint256 received = wrsETH.balanceOf(user) - before;
    // Assert received < expected at old rate, and no revert occurred
    assertLt(received, expectedAtOldRate);
}
```

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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
