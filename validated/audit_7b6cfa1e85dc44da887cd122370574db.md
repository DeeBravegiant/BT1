### Title
Yield Theft by Sandwiching `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` - (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

---

### Summary

MEV/execution-layer rewards accumulate in `FeeReceiver` and are excluded from TVL until `sendFunds()` is called. Both `sendFunds()` and `updateRSETHPrice()` are permissionless. An attacker can deposit at the stale (pre-reward) `rsETHPrice`, trigger the reward flush and price update in the same transaction bundle, then immediately initiate a withdrawal at the inflated price — stealing a proportional share of the rewards.

---

### Finding Description

The protocol separates reward accounting from live TVL. Rewards (MEV, execution-layer tips) land in `FeeReceiver` and are **not** counted in TVL until explicitly flushed:

`FeeReceiver.sendFunds()` has no access control — any caller can invoke it:

```solidity
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

Once called, the ETH lands in `LRTDepositPool` and is immediately counted in `getETHDistributionData()` via `address(this).balance`:

```solidity
ethLyingInDepositPool = address(this).balance;
``` [2](#0-1) 

`LRTOracle.updateRSETHPrice()` is also permissionless (`public whenNotPaused`), and recomputes `rsETHPrice` from the current TVL:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

Deposits use the **cached** `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

Withdrawal amounts are also locked at the **cached** `rsETHPrice` at initiation time:

```solidity
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
// ...
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [5](#0-4) [6](#0-5) 

The `_calculatePayoutAmount` in `unlockQueue` pays the **minimum** of the locked `expectedAssetAmount` and the current return — so a withdrawal initiated at a high price is protected from downside but captures the full upside locked at initiation:

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
``` [7](#0-6) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

The attacker extracts a share of MEV/execution-layer rewards proportional to their deposit size relative to total TVL. Existing depositors receive less yield than they are entitled to. The attack is repeatable every reward cycle.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. Monitoring `FeeReceiver` balance for accumulated rewards.
2. Capital to deposit (returned after the withdrawal delay, minus gas).
3. Calling two permissionless functions (`sendFunds()` + `updateRSETHPrice()`) atomically with the deposit and withdrawal initiation.

No privileged access is needed. The `pricePercentageLimit` guard in `_updateRsETHPrice()` may block the call if the price jump is large, but:
- `pricePercentageLimit` can be zero (no limit configured).
- For small reward amounts relative to TVL, the price increase stays within any configured limit.
- The attacker can also simply front-run a legitimate `sendFunds()` call and back-run with `updateRSETHPrice()` + `initiateWithdrawal()`.

---

### Recommendation

1. **Remove permissionless access from `FeeReceiver.sendFunds()`** — restrict it to a trusted role (e.g., `MANAGER`) so rewards cannot be flushed at an attacker-chosen moment.
2. **Alternatively**, update `rsETHPrice` atomically inside `receiveFromRewardReceiver()` so the price reflects the new TVL before any subsequent deposit or withdrawal in the same block can exploit the gap.
3. **Or**, snapshot the `rsETHPrice` at deposit time and use the **lower** of the deposit-time price and the withdrawal-time price when computing `expectedAssetAmount`.

---

### Proof of Concept

```
Block N (attacker's bundle):
  tx1: attacker calls LRTDepositPool.depositETH{value: X}(...)
       → rsETHPrice is stale (pre-reward), attacker receives rsETH_amount = X / rsETHPrice_old

  tx2: attacker calls FeeReceiver.sendFunds()
       → R ETH of rewards move to LRTDepositPool; TVL increases by R

  tx3: attacker calls LRTOracle.updateRSETHPrice()
       → rsETHPrice_new = (TVL_old + X + R) / rsETH_supply
       → rsETHPrice_new > rsETHPrice_old

  tx4: attacker calls LRTWithdrawalManager.initiateWithdrawal(ETH, rsETH_amount)
       → expectedAssetAmount = rsETH_amount * rsETHPrice_new / 1e18
       → expectedAssetAmount > X  (attacker locked in the higher price)

Block N + withdrawalDelayBlocks:
  tx5: operator calls unlockQueue(...)
  tx6: attacker calls completeWithdrawal(ETH)
       → receives expectedAssetAmount > X
       → profit ≈ rsETH_amount * (rsETHPrice_new - rsETHPrice_old) / 1e18
                ≈ attacker_share_of_TVL * R
```

The attacker's profit scales with their share of TVL at the time of the attack and the size of the pending reward `R` in `FeeReceiver`. Existing depositors receive correspondingly less yield.

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

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
