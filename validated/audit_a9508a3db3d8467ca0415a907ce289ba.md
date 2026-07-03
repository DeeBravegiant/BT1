### Title
Protocol Fee Applied Retroactively to All Accrued Rewards When `protocolFeeInBPS` Is Raised Before `updateRSETHPrice()` Is Called - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._updateRsETHPrice()` computes the protocol fee on the **entire reward delta since the last oracle update** using the fee rate that is current at call time. Because `LRTConfig.setProtocolFeeBps()` can be called at any moment without first settling pending rewards, a fee increase retroactively taxes rewards that accrued under the old (lower) rate. rsETH holders lose unclaimed yield they were entitled to under the prior fee schedule.

---

### Finding Description

`_updateRsETHPrice()` measures the reward as the difference between the current TVL and the TVL implied by the last stored price:

```
rewardAmount      = totalETHInProtocol - previousTVL          // all rewards since last update
protocolFeeInETH  = rewardAmount * lrtConfig.protocolFeeInBPS() / 10_000
``` [1](#0-0) 

`protocolFeeInBPS` is read live from `LRTConfig` at the moment `updateRSETHPrice()` executes. There is no snapshot of the fee rate that was in effect while the rewards were accumulating.

`LRTConfig.setProtocolFeeBps()` is callable by the `MANAGER` role at any time with no precondition that pending rewards be settled first:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
``` [2](#0-1) 

The result is that the new fee rate is applied to the **entire accumulated reward window**, including the portion that accrued while the old (lower) rate was in effect.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

rsETH holders are entitled to `(1 - oldFee)` of rewards that accrued under the old fee schedule. When the fee is raised before `updateRSETHPrice()` is called, the treasury receives `newFee × totalAccumulatedRewards` instead of `oldFee × totalAccumulatedRewards`. The difference — `(newFee - oldFee) × totalAccumulatedRewards` — is permanently redirected from rsETH holders to the protocol treasury. The yield is not frozen; it is transferred away from its rightful recipients.

---

### Likelihood Explanation

**Medium.** The `MANAGER` role is a standard operational role used for routine parameter updates. A fee adjustment is a normal governance action. The oracle update cadence is not continuous — there is always a non-zero window between the last `updateRSETHPrice()` call and the next one. Any fee increase during that window retroactively taxes the entire accumulated reward. No malicious intent is required; the loss occurs even during a routine, well-intentioned fee change.

---

### Recommendation

Require that `updateRSETHPrice()` be called (settling all pending rewards at the current fee) before `protocolFeeInBPS` is changed. This can be enforced inside `setProtocolFeeBps()`:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    // Settle pending rewards at the current fee before changing it
    ILRTOracle(contractMap[LRTConstants.LRT_ORACLE]).updateRSETHPrice();
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

Alternatively, store a per-period fee snapshot inside the oracle and apply it proportionally.

---

### Proof of Concept

1. `protocolFeeInBPS = 500` (5%). Rewards of **100 ETH** accrue in the protocol since the last oracle update (e.g., staking rewards flow through `FeeReceiver.sendFunds()` into the deposit pool).
2. MANAGER calls `LRTConfig.setProtocolFeeBps(1500)` (15%) — a routine governance action.
3. Anyone calls `LRTOracle.updateRSETHPrice()`.
4. Inside `_updateRsETHPrice()`:
   - `rewardAmount = 100 ETH`
   - `protocolFeeInETH = 100 × 1500 / 10_000 = 15 ETH` (should be `5 ETH` for the period when fee was 5%)
   - Treasury receives rsETH worth **15 ETH** instead of **5 ETH**.
5. rsETH holders permanently lose **10 ETH** of yield they were entitled to under the 5% fee schedule. [3](#0-2) [2](#0-1) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L243-250)
```text
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTConfig.sol (L196-200)
```text
    function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
        if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
        protocolFeeInBPS = _protocolFeeInBPS;
        emit UpdateFee(_protocolFeeInBPS);
    }
```

**File:** contracts/FeeReceiver.sol (L53-57)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
```
