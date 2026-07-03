Audit Report

## Title
Missing Slippage Protection in L2 Pool Deposit Functions - (File: contracts/pools/RSETHPool.sol)

## Summary
The `deposit(string)` and `deposit(address, uint256, string)` functions in `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV2ExternalBridge`, and `RSETHPoolV3ExternalBridge` compute the rsETH output solely from the live oracle rate at execution time, with no `minRSETHAmountExpected` guard. A user who observes a favorable rate and submits a deposit may receive fewer rsETH than anticipated if the oracle updates before their transaction is mined, with no on-chain mechanism to revert. The L1 `LRTDepositPool` already implements this protection, making the omission an inconsistency across the protocol.

## Finding Description
In `RSETHPool.deposit(string)` (L265–278) and `RSETHPool.deposit(address, uint256, string)` (L284–305), the rsETH output is computed by `viewSwapRsETHAmountAndFee` (L311–320), which reads the live oracle rate via `getRate()` and computes `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`. No minimum output check exists before the transfer at L275 or L302. The same pattern is present in `RSETHPoolNoWrapper` (L231–271), `RSETHPoolV2ExternalBridge` (L289–301), and `RSETHPoolV3ExternalBridge` (L366–412).

By contrast, `LRTDepositPool.depositETH` (L76–93) and `depositAsset` (L99–118) both pass `minRSETHAmountExpected` to `_beforeDeposit` (L648–670), which reverts with `MinimumAmountToReceiveNotMet` if `rsethAmountToMint < minRSETHAmountExpected`. This guard is entirely absent from all L2 pool deposit paths.

Exploit flow:
1. User calls `viewSwapRsETHAmountAndFee` off-chain, observes rate = 1.05, expects ≈ 0.952 rsETH for 1 ETH.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is mined, the oracle updates to rate = 1.10.
4. Transaction executes: user receives ≈ 0.909 rsETH — ~4.5% less than expected.
5. No parameter exists to cause the transaction to revert; the user has no recourse.

## Impact Explanation
The user does not lose ETH value outright — the rsETH received is worth approximately the ETH deposited at the new rate. However, the contract fails to deliver the rsETH amount the user reasonably expected when submitting the transaction. This matches the **Low** allowed impact: *Contract fails to deliver promised returns, but doesn't lose value*. The asymmetry with L1 `LRTDepositPool` confirms this is a protocol-level inconsistency, not merely a best-practice suggestion.

## Likelihood Explanation
The rsETH/ETH rate accretes slowly via staking rewards, so large single-block swings are uncommon. However, oracle updates can occur at any time, and during periods of high network congestion a pending transaction may sit in the mempool long enough for one or more oracle updates to occur. Every L2 pool depositor is exposed on every deposit with no opt-out mechanism. No attacker is required — normal oracle operation is sufficient to trigger the condition.

## Recommendation
Add a `minRSETHAmountExpected` parameter to both `deposit()` overloads in `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV2ExternalBridge`, and `RSETHPoolV3ExternalBridge`, and revert if the computed `rsETHAmount` falls below it, mirroring the pattern in `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

## Proof of Concept
**Foundry fork test plan:**
1. Fork an L2 (e.g., Arbitrum) at a block where `RSETHPool` is deployed.
2. Record `rate0 = RSETHPool.getRate()`.
3. Call `RSETHPool.deposit{value: 1 ether}("ref")` and record `rsETHReceived`.
4. Advance the block, simulate an oracle update to `rate1 > rate0`.
5. Call `RSETHPool.deposit{value: 1 ether}("ref")` again and record `rsETHReceived2`.
6. Assert `rsETHReceived2 < rsETHReceived` and that neither call reverted despite the rate change.
7. Confirm that calling `LRTDepositPool.depositETH{value: 1 ether}(rsETHReceived, "ref")` on L1 with the old expected amount reverts with `MinimumAmountToReceiveNotMet` under the same oracle shift, demonstrating the asymmetry. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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
