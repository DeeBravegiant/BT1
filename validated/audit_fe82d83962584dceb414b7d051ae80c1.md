### Title
`setProtocolFeeBps` does not call `updateRSETHPrice` before updating `protocolFeeInBPS`, causing the new fee rate to retroactively apply to all accumulated rewards since the last price update - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.setProtocolFeeBps` updates `protocolFeeInBPS` without first settling the current reward period by calling `LRTOracle.updateRSETHPrice`. When `updateRSETHPrice` is subsequently called, the new fee rate is applied to the entire accumulated reward amount since the last price update — not just rewards that accrued after the fee change — causing incorrect fee extraction and yield theft from rsETH holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice` computes the protocol fee as follows:

```solidity
// LRTOracle.sol lines 244-246
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

`rewardAmount` is `totalETHInProtocol - previousTVL`, where `previousTVL = rsethSupply * rsETHPrice` (the TVL at the time of the **last** `updateRSETHPrice` call). This `rewardAmount` therefore represents the **total rewards accumulated since the last price update**, which can span an arbitrary number of days.

`lrtConfig.protocolFeeInBPS()` is read live at the moment of the call. If the manager has updated `protocolFeeInBPS` between two consecutive `updateRSETHPrice` calls, the new fee rate is applied to the **entire** accumulated reward window — including the portion that accrued before the fee change.

`LRTConfig.setProtocolFeeBps` performs no settlement:

```solidity
// LRTConfig.sol lines 196-200
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

There is no call to `updateRSETHPrice` before the assignment, so the reward period that elapsed under the old fee rate is never settled at the old rate.

---

### Impact Explanation

**Fee increase scenario (High — Theft of unclaimed yield):**

Suppose `protocolFeeInBPS` was 0 for 30 days, during which 100 ETH of rewards accumulated. The manager then sets `protocolFeeInBPS = 1500` (15%). On the next `updateRSETHPrice` call, the full 100 ETH is taxed at 15%, minting 15 ETH worth of rsETH to the treasury. rsETH holders lose 15 ETH of yield that should have been theirs under the old fee rate of 0. The new fee should only apply to rewards accruing **after** the change.

**Fee decrease scenario (Medium — Permanent freezing of unclaimed yield):**

If the fee is decreased, rewards that accrued under the old higher rate are settled at the new lower rate, permanently reducing the protocol fee revenue for that period. The yield that should have gone to the treasury is instead silently redistributed to rsETH holders and can never be reclaimed.

---

### Likelihood Explanation

Medium. `setProtocolFeeBps` is a routine operational action callable by any `MANAGER`-role address. No malicious intent is required — any fee update, regardless of intent, triggers the incorrect retroactive application. `updateRSETHPrice` is a public function callable by anyone, so the incorrect settlement is triggered on the very next price update after the fee change, which can happen immediately or within the same block.

---

### Recommendation

`setProtocolFeeBps` should call `updateRSETHPrice` (or its internal equivalent `_updateRsETHPrice`) before updating `protocolFeeInBPS`, so that all rewards accumulated under the old fee rate are settled at the old rate first. Only rewards accruing after the fee change should be subject to the new rate.

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    // Settle current reward period at the old fee rate before changing it
    ILRTOracle(contractMap[LRTConstants.LRT_ORACLE]).updateRSETHPrice();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

---

### Proof of Concept

1. Protocol has been running for 30 days with `protocolFeeInBPS = 0`. During this time, 100 ETH of staking rewards accumulate. `rsETHPrice` has not been updated (or was last updated 30 days ago), so `previousTVL` reflects the TVL from 30 days ago.

2. Manager calls `LRTConfig.setProtocolFeeBps(1500)`. [1](#0-0) 

3. Anyone calls `LRTOracle.updateRSETHPrice()`. Inside `_updateRsETHPrice`:
   - `previousTVL = rsethSupply * rsETHPrice` (30-day-old price)
   - `rewardAmount = totalETHInProtocol - previousTVL = 100 ETH`
   - `protocolFeeInETH = (100 ETH * 1500) / 10_000 = 15 ETH` [2](#0-1) 

4. 15 ETH worth of rsETH is minted to the treasury. [3](#0-2) 

5. rsETH holders lose 15 ETH of yield that accrued entirely under the old fee rate of 0%. The correct outcome would have been 0 ETH in protocol fees for that 30-day window, with the new 15% rate applying only to future rewards.

### Citations

**File:** contracts/LRTConfig.sol (L196-200)
```text
    function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
        if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
        protocolFeeInBPS = _protocolFeeInBPS;
        emit UpdateFee(_protocolFeeInBPS);
    }
```

**File:** contracts/LRTOracle.sol (L244-246)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
```

**File:** contracts/LRTOracle.sol (L299-307)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
```
