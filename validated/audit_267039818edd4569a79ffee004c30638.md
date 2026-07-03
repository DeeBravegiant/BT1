### Title
Protocol Fee Minted on Gross Gains Without Netting Prior Losses — (`contracts/LRTOracle.sol`)

### Summary

`LRTOracle._updateRsETHPrice()` computes the protocol fee only when `totalETHInProtocol > previousTVL`, using the raw TVL increase as the reward base. It never offsets previously-taken fees against subsequent losses. After a loss followed by a recovery, the protocol charges fees on the full gross recovery rather than only the net gain above the prior fee-adjusted high-water mark, causing the treasury to extract more rsETH than it is entitled to — diluting all rsETH holders.

### Finding Description

In `_updateRsETHPrice()`, `previousTVL` is reconstructed each call as `rsethSupply × rsETHPrice`:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

`rsETHPrice` is the price stored at the **end** of the last update, which already had the protocol fee deducted from it. When the TVL later drops and then recovers, the comparison `totalETHInProtocol > previousTVL` becomes true again as soon as the TVL exceeds the post-loss, post-fee price level — not the pre-loss level. The fee is then charged on the entire gross recovery, even though part of that recovery merely restores value that was previously lost and for which no fee was ever refunded.

Concretely:

| Step | Event | `totalETHInProtocol` | `rsETHPrice` (stored) | `previousTVL` | Fee charged |
|------|-------|---------------------|----------------------|---------------|-------------|
| 1 | Initial | 1000 ETH | 1.000 | — | — |
| 2 | Yield +100 | 1100 ETH | ~1.099 (fee taken on 100) | 1000 | fee on 100 |
| 3 | Loss −80 | 1020 ETH | 1.019 (price drops) | 1099 | 0 (TVL < prev) |
| 4 | Recovery +80 | 1100 ETH | — | 1019 | **fee on 81** ← wrong |

At step 4, the protocol charges a fee on the full 81 ETH recovery even though the net gain since the last fee-bearing update is only ~1 ETH. The treasury mints rsETH representing value that belongs to depositors, diluting every rsETH holder.

The root cause is that `previousTVL` is derived from `rsETHPrice`, which is the **fee-reduced** price. After a loss, `rsETHPrice` falls, so `previousTVL` falls with it, creating a new, lower baseline from which the next fee is computed — even though the prior fee was already extracted at the higher level.

### Impact Explanation

Every rsETH holder is diluted. The treasury receives rsETH minted against ETH that economically belongs to depositors (it is a recovery of principal, not new yield). This is a **theft of unclaimed yield** from rsETH holders: value that should accrue to depositors is instead captured by the treasury. The magnitude scales with the size of the loss-recovery cycle and the protocol fee rate (up to 15% BPS per `setProtocolFeeBps`).

### Likelihood Explanation

EigenLayer restaking strategies are subject to slashing and market fluctuations. Any partial loss followed by a recovery — a realistic and recurring scenario — triggers this over-fee. `updateRSETHPrice()` is a public, permissionless function callable by anyone, so no privileged actor is required to trigger the fee mint. Any rsETH holder or external keeper calling `updateRSETHPrice()` at the right moment causes the dilution.

### Recommendation

Track a `feeAdjustedHighWaterMark` that is updated to `totalETHInProtocol` **after** each fee is taken, and only charge fees on TVL increases above that mark. When a loss occurs, the high-water mark should not be reduced — fees should only be charged on net new gains above the last fee-bearing TVL level. Alternatively, compute `previousTVL` as `rsethSupply × rsETHPrice` **before** the fee deduction at the prior step, so that the baseline is not artificially lowered by losses.

### Proof of Concept

1. Protocol starts: `totalETHInProtocol = 1000 ETH`, `rsETHPrice = 1.0`, `rsethSupply = 1000`.
2. Yield accrues: `totalETHInProtocol = 1100`. `previousTVL = 1000 × 1.0 = 1000`. Fee = 10% of 100 = 10 ETH. `newRsETHPrice = (1100 − 10) / 1000 = 1.09`. `rsETHPrice` stored as `1.09`.
3. Loss: `totalETHInProtocol = 1020`. `previousTVL = 1000 × 1.09 = 1090`. `1020 < 1090` → no fee. `newRsETHPrice = 1020 / 1000 = 1.02`. `rsETHPrice` stored as `1.02`.
4. Recovery: `totalETHInProtocol = 1100`. `previousTVL = 1000 × 1.02 = 1020`. `1100 > 1020` → fee = 10% of 80 = 8 ETH. `newRsETHPrice = (1100 − 8) / 1000 = 1.092`.

Net gain since step 2 fee: `1100 − 1090 = 10 ETH`. Correct fee should be 10% of 10 = 1 ETH. Actual fee charged: **8 ETH**. The treasury extracted 7 ETH of value that belongs to depositors.

Entry path: any address calls `LRTOracle.updateRSETHPrice()` (public, no access control) after a loss-recovery cycle. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-250)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
