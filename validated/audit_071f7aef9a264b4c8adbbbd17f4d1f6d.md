Audit Report

## Title
Unprotected `sendFunds()` Enables Front-Running of MEV Reward Distribution — (File: `contracts/FeeReceiver.sol`)

## Summary
`FeeReceiver.sendFunds()` carries no access control, allowing any external caller to flush accumulated MEV/execution-layer ETH rewards into `LRTDepositPool` at an arbitrary moment. An attacker can deposit ETH at the pre-reward rsETH price, call `sendFunds()` to move the accumulated rewards into the pool's TVL, and after the next oracle price update redeem rsETH at the inflated price — capturing a proportional share of rewards that should have accrued exclusively to pre-existing holders.

## Finding Description
`FeeReceiver.sendFunds()` is declared `external` with no role guard:

```solidity
// contracts/FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

`receiveFromRewardReceiver()` on `LRTDepositPool` is also unrestricted (`external payable`), so the ETH lands in the deposit pool's balance immediately. The rsETH exchange rate is a **cached** value stored in `LRTOracle.rsETHPrice`, updated only when `updateRSETHPrice()` is called. Minting uses this cached price:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

When `updateRSETHPrice()` is next called, `_getTotalEthInProtocol()` reads `address(depositPool).balance`, which now includes the flushed rewards, producing a higher price. Any holder who entered before the flush benefits proportionally — including an attacker who timed their deposit to front-run `sendFunds()`.

Attack path:
1. Attacker observes `FeeReceiver` has accumulated a meaningful ETH balance.
2. Attacker calls `LRTDepositPool.depositETH()`, minting rsETH at the current cached price.
3. Attacker calls `FeeReceiver.sendFunds()`, moving all accumulated rewards into the deposit pool.
4. The next `updateRSETHPrice()` call (public, callable by anyone) computes a higher price reflecting the new TVL.
5. Attacker redeems rsETH at the inflated price after the EigenLayer withdrawal delay.

The `pricePercentageLimit` guard in `LRTOracle._updateRsETHPrice()` only reverts the oracle update call for non-managers if the price jump exceeds the threshold — it does not prevent the deposit or `sendFunds()` call, and for typical reward accumulations relative to TVL the price jump will be within the limit.

## Impact Explanation
**High — Theft of unclaimed yield.**

The 100 ETH reward in the PoC is diluted across 11,000 rsETH instead of 10,000, so existing holders receive only 90.91 ETH of yield while the attacker captures 9.09 ETH. The stolen fraction scales with `attackerDeposit / (totalTVL + attackerDeposit + rewardBalance)`. For large reward accumulations or small TVL the stolen fraction is material. This matches the allowed impact "Theft of unclaimed yield."

## Likelihood Explanation
**Medium.**

`sendFunds()` is unconditionally public. MEV infrastructure (Flashbots bundles, private RPCs) makes atomic sequencing of deposit → `sendFunds()` trivial. The only friction is EigenLayer's withdrawal delay, which defers profit realisation but does not prevent the yield theft. The attack is profitable whenever the `FeeReceiver` balance is non-trivial relative to the attacker's capital cost, and rewards accumulate continuously between calls.

## Recommendation
Restrict `sendFunds()` to a trusted role, consistent with every other sensitive function in the contract:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Alternatively, enforce a minimum interval between calls so the distribution schedule is predictable and cannot be triggered on demand.

## Proof of Concept

```
State before attack:
  rsETH supply        = 10 000 rsETH
  Total TVL           = 10 500 ETH  →  cached price = 1.05 ETH/rsETH
  FeeReceiver balance = 100 ETH

Step 1 – Attacker calls depositETH() with 1 050 ETH:
  rsETH minted = 1 050 / 1.05 = 1 000 rsETH  (at cached price)
  New supply   = 11 000 rsETH,  pool TVL = 11 550 ETH
  Cached price unchanged = 1.05

Step 2 – Attacker calls FeeReceiver.sendFunds():
  100 ETH moves to deposit pool
  Pool balance = 11 650 ETH; cached price still 1.05

Step 3 – Anyone calls LRTOracle.updateRSETHPrice():
  previousTVL  = 11 000 × 1.05 = 11 550 ETH
  rewardAmount = 11 650 − 11 550 = 100 ETH  (fee deducted per protocol)
  new price    ≈ 11 650 / 11 000 ≈ 1.05909 ETH/rsETH

Step 4 – Attacker redeems 1 000 rsETH after withdrawal delay:
  Proceeds = 1 000 × 1.05909 = 1 059.09 ETH
  Profit   = 1 059.09 − 1 050 = 9.09 ETH  (≈ 9.1 % of the 100 ETH reward)

Foundry fork test outline:
  1. Fork mainnet; impersonate a large ETH holder.
  2. Record rsETHPrice = lrtOracle.rsETHPrice().
  3. depositETH(minRsETH, "") with 1 050 ETH.
  4. feeReceiver.sendFunds().
  5. lrtOracle.updateRSETHPrice().
  6. Assert lrtOracle.rsETHPrice() > step-2 price.
  7. Initiate withdrawal; after delay assert ETH received > 1 050 ETH.
```