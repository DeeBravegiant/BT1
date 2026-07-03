Audit Report

## Title
Missing Minimum rsETH Output Guard in L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPool.sol)

## Summary
All L2 pool `deposit()` functions compute the rsETH output amount using a live oracle rate at execution time but accept no caller-supplied minimum output parameter. Because the rsETH/ETH rate is monotonically increasing, any depositor who previews via `viewSwapRsETHAmountAndFee()` and then submits a `deposit()` transaction will receive fewer rsETH units than shown if the rate ticks upward before the transaction is mined. The L1 `LRTDepositPool` already guards against this with `minRSETHAmountExpected`; the L2 pools do not.

## Finding Description
Every L2 pool variant follows the same pattern in its `deposit()` function:

1. Accept ETH or ERC20 input.
2. Call `viewSwapRsETHAmountAndFee()`, which reads `getRate()` → `IOracle(rsETHOracle).getRate()` at execution time.
3. Transfer the computed `rsETHAmount` to the caller with no floor check.

`RSETHPool.deposit(string)` (L265–278) and `RSETHPool.deposit(address,uint256,string)` (L284–305) both follow this pattern. The rate computation at L311–319 is `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`, so any increase in `rsETHToETHrate` between preview and execution reduces `rsETHAmount`. The same pattern is present in `RSETHPoolNoWrapper` (L231–244), `RSETHPoolV3` (L246–265), `RSETHPoolV3ExternalBridge` (L366–384), and `RSETHPoolV3WithNativeChainBridge` (L282–301).

By contrast, `LRTDepositPool._beforeDeposit()` (L648–670) explicitly checks `if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet()`, enforced on every deposit path via `depositETH()` (L76–93) and `depositAsset()` (L99–118).

The oracle used on L2 (`InterimRSETHOracle` or a cross-chain rate receiver) returns a rate that is updated periodically and only increases. There is no on-chain mechanism in any L2 pool to reject a deposit when the computed output falls below the user's expectation.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but does not lose value.**

The depositor's ETH/token value is fully preserved: the rsETH units received, multiplied by the new (higher) rate, equal the same ETH value as the previewed amount at the old rate. However, the number of rsETH units delivered is lower than the amount shown by `viewSwapRsETHAmountAndFee()`. A user who requires a precise rsETH amount — e.g., to meet a collateral threshold in a downstream DeFi protocol — cannot guarantee that amount will be delivered and has no on-chain recourse to abort the transaction.

## Likelihood Explanation
The rsETH rate increases on every oracle update cycle. Any depositor who previews the swap and submits a transaction faces this discrepancy. No attacker action is required; the rate drift is a normal, continuous protocol behaviour. The gap widens during periods of network congestion where transactions sit in the mempool for multiple blocks or when the oracle is updated between preview and execution.

## Recommendation
Add a `minRsETHAmount` parameter to all `deposit()` overloads in every L2 pool contract (`RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) and revert if the computed `rsETHAmount` falls below it, mirroring the `minRSETHAmountExpected` guard already present in `LRTDepositPool._beforeDeposit()`:

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

## Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` at rate `R` → sees they will receive `X = 1e18 * 1e18 / R` rsETH (after fee).
2. User submits `deposit{value: 1 ether}(referralId)`.
3. Before the transaction is mined, the oracle rate updates from `R` to `R' > R` (normal reward accrual).
4. Inside the transaction, `viewSwapRsETHAmountAndFee` computes `X' = 1e18 * 1e18 / R'` where `R' > R`, so `X' < X`.
5. User receives `X'` rsETH — fewer than previewed — with no on-chain protection and no revert.

**Foundry fork test plan:**
```solidity
function testDepositSlippage() public {
    uint256 preview = pool.viewSwapRsETHAmountAndFee(1 ether);
    // Simulate oracle rate increase (e.g., via InterimRSETHOracle.setRate)
    oracle.setRate(oracle.getRate() + 1e14); // small rate tick
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref");
    uint256 received = wrsETH.balanceOf(user);
    assertLt(received, preview); // user received less than previewed, no revert
}
```

The same sequence applies to token deposits across all five affected pool contracts. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
