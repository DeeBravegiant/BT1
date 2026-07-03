Based on my analysis, I found a valid analog in `contracts/LRTOracle.sol`. The `_updateRsETHPrice()` function applies `protocolFeeInBPS` to the entire TVL increase since the last oracle update. If `LRTConfig.setProtocolFeeBps()` is called without first calling `updateRSETHPrice()`, the new fee rate is retroactively applied to yield that accrued under the old rate — the exact same state-update-ordering flaw as M-05.

---

### Title
`setProtocolFeeBps` fails to update rsETH price before updating protocol fee — (`contracts/LRTConfig.sol`)

### Summary

`LRTConfig.setProtocolFeeBps()` updates `protocolFeeInBPS` without first calling `LRTOracle.updateRSETHPrice()`. Because `_updateRsETHPrice()` applies the current `protocolFeeInBPS` to the **entire TVL increase since the last price update**, changing the fee mid-period causes the new rate to be retroactively applied to yield that accrued under the old rate, unfairly altering the rsETH exchange rate for all holders.

### Finding Description

`LRTOracle._updateRsETHPrice()` computes the protocol fee as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
// ...
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [1](#0-0) 

`rewardAmount` is the **cumulative** TVL growth since the last `updateRSETHPrice()` call. The live value of `lrtConfig.protocolFeeInBPS()` is read at call time and applied to this entire accumulated amount.

`setProtocolFeeBps` in `LRTConfig.sol` simply overwrites the fee with no prior oracle settlement:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
``` [2](#0-1) 

If the fee is raised from, say, 5% to 15% and `updateRSETHPrice()` has not been called for several days, the next oracle update will apply the 15% fee to all yield that accrued during those days — yield that rsETH holders earned under the 5% regime. Conversely, a fee decrease retroactively benefits holders at the protocol's expense.

### Impact Explanation

**High — Theft of unclaimed yield.** rsETH holders lose yield they legitimately earned under the prior fee rate when the fee is increased retroactively. The magnitude scales with (a) the size of the TVL increase since the last oracle update and (b) the magnitude of the fee change. In a protocol with hundreds of millions in TVL and infrequent oracle updates, even a modest fee increase applied retroactively over several days can represent a material transfer of value away from rsETH holders.

### Likelihood Explanation

**Medium.** The MANAGER role is expected to adjust `protocolFeeInBPS` as part of normal protocol governance. There is no documentation or code-level guard requiring `updateRSETHPrice()` to be called first. Any legitimate fee change — even one made in good faith — triggers this issue if the oracle has not been updated in the same block.

### Recommendation

Add a call to `updateRSETHPrice()` inside `setProtocolFeeBps()` before overwriting the fee, mirroring the fix applied to `V3Vault.setReserveFactor()`:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    // Settle accrued yield at the current fee before changing it
    ILRTOracle(getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

### Proof of Concept

1. Protocol TVL is 1 000 ETH. `rsETHPrice = 1.05 ETH`. `protocolFeeInBPS = 500` (5%). Last oracle update was 7 days ago.
2. Over those 7 days, staking rewards increase TVL to 1 007 ETH (`rewardAmount = 7 ETH`).
3. MANAGER calls `setProtocolFeeBps(1500)` (15%) — no oracle update occurs.
4. Anyone calls `updateRSETHPrice()`. The formula applies 15% to the full 7-day `rewardAmount`:
   - `protocolFeeInETH = 7 * 1500 / 10_000 = 1.05 ETH` (instead of `0.35 ETH` at 5%)
   - rsETH holders lose `0.70 ETH` of yield they earned under the old 5% regime.
5. The new `rsETHPrice` is lower than it would have been had the fee been settled before the change. [3](#0-2) [2](#0-1)

### Citations

**File:** contracts/LRTOracle.sol (L243-251)
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
