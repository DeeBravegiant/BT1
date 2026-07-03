Audit Report

## Title
Lack of Slippage Protection in L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV2.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol)

## Summary
All L2 pool `deposit()` functions compute the rsETH/wrsETH output using a live oracle rate at execution time but accept no `minRsETHAmountExpected` parameter from the caller. If the oracle rate updates between transaction submission and mining, the user receives fewer rsETH tokens than anticipated with no ability to revert. The L1 `LRTDepositPool._beforeDeposit()` already implements this protection, making the omission on L2 a clear inconsistency.

## Finding Description
Every L2 pool `deposit()` function follows the same pattern: fetch the live oracle rate via `getRate()`, compute `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`, and transfer/mint that amount to the caller — with no floor check.

In `RSETHPoolV2ExternalBridge.deposit()`: [1](#0-0) 

The rate computation in `viewSwapRsETHAmountAndFee()`: [2](#0-1) 

The same pattern is present in `RSETHPool.deposit()`: [3](#0-2) 

And in `RSETHPoolV3ExternalBridge.deposit()`: [4](#0-3) 

By contrast, `LRTDepositPool._beforeDeposit()` on L1 accepts `minRSETHAmountExpected` and reverts with `MinimumAmountToReceiveNotMet` if the minted amount falls short: [5](#0-4) 

A `getMinAmount()` helper already exists in `RSETHPoolV3ExternalBridge` for off-chain use but is never enforced on-chain: [6](#0-5) 

The root cause is the absence of any on-chain minimum-output guard in all six L2 pool contracts. No existing modifier or check compensates for this — `nonReentrant`, `whenNotPaused`, and `limitDailyMint` are orthogonal to output amount validation.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

When a user submits a deposit and the oracle rate increases before the transaction is mined (rsETH appreciates in ETH terms), the user receives fewer rsETH tokens than they expected at submission time. The tokens received are worth the same ETH value as the deposited amount (minus fee), so no ETH value is lost. However, the contract fails to deliver the specific rsETH quantity the user was promised at the time of transaction construction. This matches the allowed Low impact: "Contract fails to deliver promised returns, but doesn't lose value."

## Likelihood Explanation
The `rsETHToETHrate` oracle is updated periodically by the protocol across all L2 deployments (Arbitrum, Unichain, etc.). Any deposit transaction pending in the mempool at the time of an oracle update will execute at the new rate. No attacker capability is required — routine oracle updates are sufficient. This is a normal operational occurrence, not a contrived scenario. Any unprivileged depositor calling `deposit()` on any of the six affected contracts can be affected.

## Recommendation
Add a `minRsETHAmountExpected` parameter to all `deposit()` functions in the L2 pool contracts, mirroring the L1 pattern:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

The existing `getMinAmount(uint256 amount, uint256 slippageTolerance)` in `RSETHPoolV3ExternalBridge` can be used off-chain to compute the appropriate `minRsETHAmountExpected` before submitting the transaction. [6](#0-5) 

## Proof of Concept
1. Oracle reports `rsETHToETHrate = 1.05e18`.
2. User calls `RSETHPoolV2ExternalBridge.deposit{value: 1 ether}("ref")`, expecting `≈ 0.952 wrsETH` (assuming 0 fee for simplicity).
3. Before the tx is mined, the protocol oracle updates to `rsETHToETHrate = 1.10e18`.
4. `viewSwapRsETHAmountAndFee(1e18)` computes `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909e18`.
5. `wrsETH.mint(msg.sender, 0.909e18)` executes successfully.
6. User receives `≈ 0.909 wrsETH` instead of `≈ 0.952 wrsETH` — a ~4.5% shortfall in token count — with no revert.

A Foundry fork test can reproduce this by: (a) deploying or forking the pool, (b) setting the oracle rate to `1.05e18`, (c) submitting the deposit, (d) updating the oracle to `1.10e18` in the same test block before the deposit executes, and (e) asserting the minted amount equals `≈ 0.909e18` rather than `≈ 0.952e18`. [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-316)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev view function to get the rsETH amount for a given amount of ETH
    /// @param amount The amount of ETH
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L540-544)
```text
    function getMinAmount(uint256 amount, uint256 slippageTolerance) external pure returns (uint256) {
        if (slippageTolerance > 10_000) revert InvalidSlippageTolerance();

        return amount - (amount * slippageTolerance / 10_000);
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
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
```
