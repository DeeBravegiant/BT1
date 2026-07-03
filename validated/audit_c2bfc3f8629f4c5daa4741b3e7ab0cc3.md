Audit Report

## Title
Unrestricted `sendFunds()` in `FeeReceiver` Enables Stale-Price Deposit to Steal Unclaimed Yield - (File: contracts/FeeReceiver.sol)

## Summary
`FeeReceiver.sendFunds()` has no access-control modifier, allowing any external caller to flush accumulated MEV/execution-layer rewards into `LRTDepositPool` at will. Because `LRTOracle.updateRSETHPrice()` is also publicly callable and reverts when a price increase exceeds `pricePercentageLimit`, an attacker can force the reward flush, cause the price update to revert, and then deposit at the stale (under-valued) `rsETHPrice`, minting more rsETH than the true exchange rate warrants and stealing unclaimed yield from existing rsETH holders.

## Finding Description
`FeeReceiver.sendFunds()` is declared `external` with no role check:

```solidity
// contracts/FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

`LRTDepositPool.receiveFromRewardReceiver()` is equally unrestricted:

```solidity
// contracts/LRTDepositPool.sol L61
function receiveFromRewardReceiver() external payable { }
```

Once the ETH lands in the deposit pool, `getETHDistributionData()` immediately counts it in TVL:

```solidity
// contracts/LRTDepositPool.sol L480
ethLyingInDepositPool = address(this).balance;
```

`LRTOracle.updateRSETHPrice()` is publicly callable (`public whenNotPaused`). When the TVL spike from the reward flush causes `newRsETHPrice` to exceed `highestRsethPrice` by more than `pricePercentageLimit`, the function reverts for non-manager callers:

```solidity
// contracts/LRTOracle.sol L252-265
if (newRsETHPrice > highestRsethPrice) {
    uint256 priceDifference = newRsETHPrice - highestRsethPrice;
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceIncreaseOffLimit) {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert PriceAboveDailyThreshold();
        }
    }
}
```

The revert leaves `rsETHPrice` at its old, lower value. Deposits then use this stale price as the denominator:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

A lower `rsETHPrice` denominator causes the attacker to receive more rsETH per ETH deposited than the true exchange rate warrants. When the manager later calls `updateRSETHPriceAsManager()`, the price rises to reflect the rewards, and the attacker's over-minted rsETH appreciates at the expense of all pre-existing holders.

## Impact Explanation
**High — Theft of unclaimed yield.**

Existing rsETH holders are entitled to the price appreciation that accumulated MEV rewards represent. By forcing the reward flush and depositing at the stale price, the attacker captures a disproportionate share of that appreciation. The attacker's rsETH balance is inflated relative to the true TVL, permanently diluting every pre-existing holder's claim on the underlying assets. This maps directly to the allowed impact: *Theft of unclaimed yield*.

## Likelihood Explanation
**Medium.**

- `pricePercentageLimit` is explicitly configurable via `setPricePercentageLimit()` and the guard logic is written to enforce it, making it likely to be active in production.
- MEV/execution-layer rewards accumulate continuously in `FeeReceiver`; over days or weeks the balance can be large enough to push a single-block TVL spike past the configured threshold.
- The attack requires only two public calls (`sendFunds()` + `updateRSETHPrice()`) followed by a standard deposit — no special privileges, no flash loan, no front-running of a specific transaction.
- The attack is repeatable each time rewards accumulate to a sufficient level.

## Recommendation
Restrict `sendFunds()` to an authorized role so only trusted keepers can trigger reward accounting:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Additionally, restrict `receiveFromRewardReceiver()` in `LRTDepositPool` to only accept calls from the registered `FeeReceiver` address, preventing direct ETH injection from arbitrary callers.

## Proof of Concept
```
Setup:
  FeeReceiver holds 500 ETH in accumulated MEV rewards.
  rsETHPrice = highestRsethPrice = 1.05e18.
  pricePercentageLimit = 1e16 (1%).
  Total rsETH supply = S, TVL = T.

Step 1: Attacker calls FeeReceiver.sendFunds().
  → 500 ETH moves to LRTDepositPool.
  → LRTDepositPool.address(this).balance increases by 500 ETH.
  → getETHDistributionData() now returns ethLyingInDepositPool += 500 ETH.

Step 2: Attacker calls LRTOracle.updateRSETHPrice().
  → _getTotalEthInProtocol() returns T + 500 ETH.
  → newRsETHPrice = (T + 500) / S >> 1.05e18 * 1.01.
  → isPriceIncreaseOffLimit = true.
  → Reverts with PriceAboveDailyThreshold().
  → rsETHPrice remains at 1.05e18 (stale).

Step 3: Attacker calls LRTDepositPool.depositETH{ value: X }(minRSETH, "").
  → getRsETHAmountToMint uses rsETHPrice = 1.05e18 (stale).
  → Attacker receives X / 1.05e18 rsETH instead of the correct X / truePrice rsETH.
  → Attacker is over-minted by (X / 1.05e18) - (X / truePrice) rsETH.

Step 4: Manager calls LRTOracle.updateRSETHPriceAsManager().
  → rsETHPrice updates to the true higher value reflecting the 500 ETH reward.
  → Attacker's over-minted rsETH is now worth more than the ETH deposited,
    at the expense of all pre-existing rsETH holders whose share is diluted.
```