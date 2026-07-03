Audit Report

## Title
Yield Theft via Sandwiching `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` - (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

## Summary
`FeeReceiver.sendFunds()` is permissionless, allowing any caller to flush accumulated MEV/execution-layer rewards into `LRTDepositPool` at a chosen moment. Combined with the equally permissionless `LRTOracle.updateRSETHPrice()`, an attacker can deposit at the stale pre-reward price, flush rewards, update the price, and immediately initiate a withdrawal at the inflated price — locking in a proportional share of the reward cycle's yield that belongs to existing depositors.

## Finding Description
`FeeReceiver.sendFunds()` carries no access control:

```solidity
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

Once called, the ETH lands in `LRTDepositPool` and is immediately reflected in `getETHDistributionData()` via `address(this).balance`: [2](#0-1) 

`LRTOracle.updateRSETHPrice()` is also permissionless (`public whenNotPaused`): [3](#0-2) 

Deposits use the cached `rsETHPrice` at the time of deposit: [4](#0-3) 

`initiateWithdrawal` locks in `expectedAssetAmount` using the current `rsETHPrice` at initiation time: [5](#0-4) [6](#0-5) 

`_calculatePayoutAmount` in `unlockQueue` pays the **minimum** of the locked `expectedAssetAmount` and the current return — so a withdrawal initiated at a high price is protected from downside but captures the full upside locked at initiation: [7](#0-6) 

**Exploit flow:**
1. Attacker deposits ETH at stale `rsETHPrice_old` (pre-reward), receiving `rsETH_amount = X / rsETHPrice_old`.
2. Attacker calls `FeeReceiver.sendFunds()` — R ETH of rewards move to `LRTDepositPool`; TVL increases by R.
3. Attacker calls `LRTOracle.updateRSETHPrice()` — `rsETHPrice_new = (TVL + R) / rsETH_supply > rsETHPrice_old`.
4. Attacker calls `LRTWithdrawalManager.initiateWithdrawal()` — `expectedAssetAmount = rsETH_amount * rsETHPrice_new / 1e18 > X`.
5. After `withdrawalDelayBlocks`, attacker calls `completeWithdrawal()` and receives `expectedAssetAmount > X`.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` can block the call if the price jump exceeds the configured threshold — but only if `pricePercentageLimit > 0`. If it is zero (no limit configured), or if the reward amount is small relative to TVL (keeping the price increase within the limit), the guard is bypassed entirely. [8](#0-7) 

## Impact Explanation
**High — Theft of unclaimed yield.** The attacker extracts a share of MEV/execution-layer rewards proportional to their deposit size relative to total TVL. Existing depositors receive correspondingly less yield than they are entitled to. The attack is repeatable every reward cycle and requires no privileged access.

## Likelihood Explanation
**Medium.** The attacker needs only: (1) capital to deposit (returned after the withdrawal delay minus gas), (2) monitoring of `FeeReceiver` balance, and (3) two permissionless function calls (`sendFunds()` + `updateRSETHPrice()`) bundled with deposit and withdrawal initiation. No privileged access is required. For small reward amounts relative to TVL, the `pricePercentageLimit` guard does not trigger. The attacker can also front-run a legitimate `sendFunds()` call and back-run with `updateRSETHPrice()` + `initiateWithdrawal()`.

## Recommendation
1. **Restrict `FeeReceiver.sendFunds()`** to a trusted role (e.g., `MANAGER`) so rewards cannot be flushed at an attacker-chosen moment.
2. **Alternatively**, call `_updateRsETHPrice()` atomically inside `receiveFromRewardReceiver()` so the price reflects the new TVL before any subsequent deposit or withdrawal in the same block can exploit the gap.
3. **Or**, snapshot `rsETHPrice` at deposit time and use the **lower** of the deposit-time price and the withdrawal-time price when computing `expectedAssetAmount`.

## Proof of Concept
```
Block N (attacker's bundle):
  tx1: attacker calls LRTDepositPool.depositETH{value: X}(...)
       → rsETHPrice is stale (pre-reward)
       → attacker receives rsETH_amount = X / rsETHPrice_old

  tx2: attacker calls FeeReceiver.sendFunds()
       → R ETH of rewards move to LRTDepositPool; TVL increases by R

  tx3: attacker calls LRTOracle.updateRSETHPrice()
       → rsETHPrice_new = (TVL_old + X + R) / rsETH_supply
       → rsETHPrice_new > rsETHPrice_old

  tx4: attacker calls LRTWithdrawalManager.initiateWithdrawal(ETH, rsETH_amount)
       → expectedAssetAmount = rsETH_amount * rsETHPrice_new / 1e18
       → expectedAssetAmount > X

Block N + withdrawalDelayBlocks:
  tx5: operator calls unlockQueue(...)
       → _calculatePayoutAmount returns min(expectedAssetAmount, currentReturn)
       → since price has not dropped, attacker receives expectedAssetAmount

  tx6: attacker calls completeWithdrawal(ETH)
       → receives expectedAssetAmount > X
       → profit ≈ rsETH_amount * (rsETHPrice_new - rsETHPrice_old) / 1e18
                ≈ attacker_share_of_TVL * R
```

A Foundry fork test can confirm this by: (1) seeding `FeeReceiver` with ETH, (2) depositing as attacker, (3) calling `sendFunds()` + `updateRSETHPrice()`, (4) calling `initiateWithdrawal()`, (5) rolling forward `withdrawalDelayBlocks`, (6) calling `unlockQueue()` + `completeWithdrawal()`, and asserting the attacker's final ETH balance exceeds their initial deposit by approximately `attacker_share * R`.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L580-593)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
