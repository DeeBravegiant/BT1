Audit Report

## Title
FeeReceiver ETH Excluded from rsETH Price Calculation Until `sendFunds()` Is Called — (`contracts/FeeReceiver.sol`, `contracts/LRTDepositPool.sol`)

## Summary

`FeeReceiver` accumulates MEV/execution-layer rewards via its `receive()` fallback but only forwards them to `LRTDepositPool` when `sendFunds()` is explicitly called. `LRTDepositPool.getETHDistributionData()` never reads `FeeReceiver`'s balance, so `LRTOracle.rsETHPrice` is understated for the entire period between reward arrival and the next `sendFunds()` call. Any user who initiates or completes a withdrawal during this window receives fewer assets than the protocol actually controls on their behalf, with no recovery path for the shortfall.

## Finding Description

`FeeReceiver.sendFunds()` is the sole mechanism to move accumulated rewards into the accounting perimeter: [1](#0-0) 

`LRTDepositPool.getETHDistributionData()` sums ETH across the deposit pool, NDCs, the unstaking vault, and the converter — but never `FeeReceiver`. The code explicitly documents this omission: [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` derives total protocol ETH exclusively through `getTotalAssetDeposits()` → `getETHDistributionData()`: [3](#0-2) 

`rsETHPrice` is then computed from that incomplete total: [4](#0-3) 

**Exploit path for `initiateWithdrawal`:**
1. MEV rewards accumulate in `FeeReceiver`; `sendFunds()` has not been called.
2. `updateRSETHPrice()` runs, producing an understated `rsETHPrice`.
3. User calls `initiateWithdrawal(asset, rsETHUnstaked)`. `expectedAssetAmount` is locked in at the understated price via `getExpectedAssetAmount`: [5](#0-4) 
4. `sendFunds()` is later called; `updateRSETHPrice()` produces a higher price.
5. `unlockQueue` calls `_calculatePayoutAmount`, which returns `min(expectedAssetAmount, currentReturn)`: [6](#0-5) 
6. Because `expectedAssetAmount` was locked at the lower price, the user receives the lower amount permanently — the rsETH is burned and the shortfall accrues to remaining holders.

**Exploit path for `instantWithdrawal`:**
1. Same precondition: FeeReceiver holds unforwarded ETH, price is understated.
2. User calls `instantWithdrawal`; `assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked)` is computed at the understated price: [7](#0-6) 
3. rsETH is burned immediately at the understated price; the user receives fewer assets with no recourse.

## Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The withdrawing user receives fewer assets than the protocol controls on their behalf. The shortfall is not lost from the protocol — it remains and benefits remaining rsETH holders — but the withdrawing user's rsETH is burned at a price that does not reflect the full backing. The magnitude scales with the FeeReceiver balance at the time of withdrawal and the time elapsed since the last `sendFunds()` call.

## Likelihood Explanation

`sendFunds()` carries no access control and is callable by anyone, so a sophisticated user can self-mitigate by calling it before withdrawing. However, there is no on-chain enforcement that `sendFunds()` is called before each price update or withdrawal. The gap is always present between MEV reward arrival and the next `sendFunds()` invocation. If the operator bot lags, is paused, or is not running, rewards accumulate silently. Ordinary users are unlikely to know they must call `sendFunds()` first. The condition is passive and continuously present rather than requiring active exploitation.

## Recommendation

Include `FeeReceiver`'s balance directly inside `getETHDistributionData()` by reading the balance of the address registered as `LRTConstants.REWARD_RECEIVER` (or `LRT_FEE_RECEIVER`), so the price oracle always reflects the full protocol-controlled ETH without requiring an intermediate `sendFunds()` call. Alternatively, call `sendFunds()` atomically at the start of `_updateRsETHPrice()` before computing the new price, ensuring rewards are always forwarded before any price snapshot is taken.

## Proof of Concept

```solidity
// Fork test (Foundry)
// 1. Deploy/fork with FeeReceiver pointing at LRTDepositPool.
// 2. vm.deal(address(feeReceiver), 10 ether);  // simulate MEV rewards
// 3. lrtOracle.updateRSETHPrice();
//    uint256 price1 = lrtOracle.rsETHPrice();
//    // price1 does NOT include the 10 ETH in FeeReceiver
// 4. As a normal user: lrtWithdrawalManager.initiateWithdrawal(ETH, rsETHAmount, "");
//    // expectedAssetAmount locked at price1
// 5. feeReceiver.sendFunds();
//    lrtOracle.updateRSETHPrice();
//    uint256 price2 = lrtOracle.rsETHPrice();
//    assert(price2 > price1);
// 6. Operator calls unlockQueue(...); user calls completeWithdrawal(...).
//    // _calculatePayoutAmount returns min(expectedAssetAmount_at_price1, currentReturn_at_price2)
//    // = expectedAssetAmount_at_price1  (the lower value)
// 7. Assert user received fewer assets than rsETHAmount * price2 / assetPrice.
//    The difference is the permanently diluted yield.
```

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L464-480)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
