### Title
Fee Double-Counting on Price Recovery Due to Lowered `rsETHPrice` Baseline - (File: contracts/LRTOracle.sol)

### Summary
In `LRTOracle._updateRsETHPrice()`, the protocol fee is computed against `previousTVL = rsethSupply * rsETHPrice`, where `rsETHPrice` is always updated to the latest price — including when the price drops. Because `rsETHPrice` is lowered on every price-decrease update, a subsequent price recovery is incorrectly treated as new yield, causing the protocol to mint fee rsETH to the treasury on principal recovery rather than on genuine new gains.

### Finding Description

`_updateRsETHPrice()` computes the fee baseline using the last stored `rsETHPrice`:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);   // line 234
...
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
``` [1](#0-0) 

At the end of every call, `rsETHPrice` is unconditionally updated to the new price — even when it is lower than before:

```solidity
rsETHPrice = newRsETHPrice;   // line 313
``` [2](#0-1) 

The contract already maintains `highestRsethPrice` as a true high-water mark, but it is only used for the price-threshold guard and the downside-protection pause — **not** for the fee baseline:

```solidity
if (newRsETHPrice > highestRsethPrice) {
    highestRsethPrice = newRsETHPrice;   // only updated upward
}
``` [3](#0-2) 

**Scenario:**

| Step | Event | `rsETHPrice` | `highestRsethPrice` | Fee charged |
|------|-------|-------------|---------------------|-------------|
| 1 | Initial: 1000 ETH deposited, price = 1.0 | 1.0 | 1.0 | — |
| 2 | Yield accrues, price = 1.2 | 1.2 | 1.2 | ✅ on 0.2 gain |
| 3 | LST price dips, rsETH price = 0.9 | **0.9** | 1.2 | none |
| 4 | Price recovers to 1.2 | 1.2 | 1.2 | ❌ fee on 0.3 "gain" (recovery of loss) |

At step 4, `previousTVL` is computed from `rsETHPrice = 0.9`, so `rewardAmount = supply * (1.2 − 0.9) = supply * 0.3`. The protocol charges fees on this amount even though no new yield was generated — the price merely returned to its prior peak. The fee is minted as new rsETH to the treasury, diluting all existing rsETH holders.

The downside-protection pause only triggers when the price drop exceeds `pricePercentageLimit`. If `pricePercentageLimit` is zero (disabled) or the drop is within the configured limit, the protocol continues operating, `rsETHPrice` is lowered, and the double-fee scenario is fully reachable. [4](#0-3) 

### Impact Explanation

**High — Theft of unclaimed yield.**

The protocol mints rsETH to the treasury on recovery of losses. This dilutes all rsETH holders proportionally: their share of the underlying ETH is reduced by the incorrectly minted fee tokens. The effect compounds every time the price dips and recovers, and the magnitude scales with the size of the dip and the total rsETH supply.

### Likelihood Explanation

**Medium.** LST prices (stETH, cbETH, rETH, etc.) fluctuate relative to ETH due to secondary-market discounts, slashing events, and oracle lag. A dip followed by recovery is a routine market event. `updateRSETHPrice()` is a public, permissionless function callable by anyone, so no privileged actor is required to trigger the mispricing. [5](#0-4) 

### Recommendation

Replace the fee baseline with `highestRsethPrice` (the existing high-water mark) instead of `rsETHPrice`:

```solidity
// Use highestRsethPrice as the fee baseline so recovery of losses is never charged
uint256 previousTVL = rsethSupply.mulWad(highestRsethPrice);
```

This ensures fees are only charged on TVL that genuinely exceeds the previous all-time high, exactly mirroring the intent of `highestRsethPrice`.

### Proof of Concept

1. 1000 stETH deposited; stETH/ETH oracle price = 1.0 → `rsETHPrice = 1.0`, `highestRsethPrice = 1.0`.
2. Staking rewards accrue; oracle price = 1.2 → `_updateRsETHPrice()` called. `previousTVL = 1000 * 1.0 = 1000`. `totalETHInProtocol = 1200`. Fee taken on 200 ETH. `rsETHPrice = 1.2`, `highestRsethPrice = 1.2`.
3. stETH trades at a discount; oracle price = 0.9 → `_updateRsETHPrice()` called. `previousTVL = 1000 * 1.2 = 1200`. `totalETHInProtocol = 900 < previousTVL` → no fee. `rsETHPrice = 0.9` (lowered). `highestRsethPrice` stays at 1.2.
4. stETH recovers; oracle price = 1.2 → `_updateRsETHPrice()` called by any external address. `previousTVL = 1000 * 0.9 = 900`. `totalETHInProtocol = 1200`. `rewardAmount = 300`. Protocol charges fee on 300 ETH of "yield" — but this is pure recovery of the step-3 loss. Fee rsETH is minted to treasury, diluting all holders.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-247)
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
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L294-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
