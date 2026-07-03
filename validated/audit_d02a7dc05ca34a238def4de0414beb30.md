Audit Report

## Title
Unrestricted `sendFunds()` Enables Timing Manipulation of MEV Reward Distribution to Steal Yield from rsETH Holders - (File: contracts/FeeReceiver.sol)

## Summary
`FeeReceiver.sendFunds()` has no access control, allowing any external caller to push accumulated MEV/fee rewards into the deposit pool at an arbitrary time. An attacker can deposit ETH at the current stale rsETH price (before rewards are reflected), trigger `sendFunds()` to push accumulated rewards into the pool, then call the public `LRTOracle.updateRSETHPrice()` to update the price upward, and finally initiate a withdrawal at the inflated price — capturing yield that belongs to pre-existing rsETH holders.

## Finding Description
`FeeReceiver.sendFunds()` is declared `external` with no role check:

```solidity
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

In contrast, the sibling admin function `setDepositPool` correctly enforces `onlyRole(LRTConstants.MANAGER)`, confirming the missing modifier on `sendFunds()` is an anomaly: [2](#0-1) 

`receiveFromRewardReceiver()` on the deposit pool is also unrestricted (`external payable`), so the ETH transfer succeeds unconditionally: [3](#0-2) 

The rsETH price used for both minting and withdrawal calculations is a **stored stale value** in `LRTOracle.rsETHPrice`, only updated when `updateRSETHPrice()` is explicitly called: [4](#0-3) 

`updateRSETHPrice()` is public with no role restriction (only a `whenNotPaused` guard): [5](#0-4) 

The complete exploit path is:
1. Observe `FeeReceiver.balance = R` (accumulated MEV rewards).
2. Call `LRTDepositPool.depositETH{value: D}()` — mints rsETH at stale price `P` (which does not yet include `R`).
3. Call `FeeReceiver.sendFunds()` — pushes `R` ETH into the deposit pool, increasing real TVL without minting new rsETH.
4. Call `LRTOracle.updateRSETHPrice()` — updates stored price to `P' = (TVL + R) / rsETH_supply > P`.
5. Call `LRTWithdrawalManager.initiateWithdrawal()` — withdrawal amount locked at the inflated price `P'`.
6. After the withdrawal delay, call `completeWithdrawal()` — receives `D * P'/P` ETH, netting `D*(P'-P)/P` ETH of stolen yield.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` can block step 4 if the price jump exceeds the configured threshold for non-managers: [6](#0-5) 

However, this guard is bypassed entirely when `pricePercentageLimit == 0` (the check is `pricePercentageLimit > 0 && ...`), and even when set, the attacker can wait for rewards to accumulate to just below the threshold before executing.

## Impact Explanation
**High — Theft of unclaimed yield.** Pre-existing rsETH holders earn MEV rewards continuously; those rewards are held in `FeeReceiver` and should accrue proportionally to all holders at the time of distribution. By front-running the distribution with a deposit, the attacker dilutes the reward share of existing holders. The stolen amount equals `R * D / (TVL + D)`, where `R` is the accumulated reward, `D` is the attacker's deposit, and `TVL` is the existing pool size. With a 10 ETH reward and a 100 ETH deposit into a 100 ETH pool, the attacker steals 5 ETH of yield from existing holders.

## Likelihood Explanation
The attack requires no special permissions. The only prerequisites are: (a) sufficient capital for the deposit, (b) `pricePercentageLimit` is either unset or the accumulated reward is within the threshold, and (c) willingness to lock capital for the withdrawal delay period (~8 days). MEV rewards accumulate continuously, so the opportunity recurs. The attacker can monitor `FeeReceiver.balance` on-chain and execute when the reward-to-TVL ratio is profitable relative to the capital cost.

## Recommendation
Add `onlyRole(LRTConstants.MANAGER)` to `sendFunds()`, consistent with the access pattern used by `setDepositPool`. Alternatively, implement a minimum time interval between calls to prevent timing manipulation. A deeper fix would snapshot rsETH balances at reward-accrual time rather than distribution time, but role-gating `sendFunds()` is the minimal correct fix.

## Proof of Concept
```solidity
// Fork test outline (Foundry)
// 1. Deploy/fork with FeeReceiver holding R ETH in accumulated rewards
// 2. vm.startPrank(attacker)
// 3. lrtDepositPool.depositETH{value: D}(minRsETH, ""); 
//    // attacker receives rsETH at stale price P
// 4. feeReceiver.sendFunds();
//    // R ETH pushed to deposit pool; real TVL increases, rsETHPrice still stale
// 5. lrtOracle.updateRSETHPrice();
//    // stored price updates to P' > P (reverts if pricePercentageLimit exceeded)
// 6. lrtWithdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHBalance, "");
//    // withdrawal locked at P': expectedAssetAmount = rsETHBalance * P' / 1e18
// 7. vm.roll(block.number + withdrawalDelayBlocks);
//    // operator calls unlockQueue(...)
// 8. lrtWithdrawalManager.completeWithdrawal(ETH_TOKEN, "");
//    // attacker receives D * P'/P ETH
// 9. assert(attackerETHBalance > D); // profit = R * D / (TVL + D)
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

**File:** contracts/FeeReceiver.sol (L66-72)
```text
    function setDepositPool(address _depositPool) external onlyRole(LRTConstants.MANAGER) {
        if (_depositPool == address(0)) revert InvalidEmptyValue();

        depositPool = _depositPool;

        emit DepositPoolSet(_depositPool);
    }
```

**File:** contracts/LRTDepositPool.sol (L60-61)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }
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

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```
