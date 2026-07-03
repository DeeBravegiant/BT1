Audit Report

## Title
Fee Double-Counting on Price Recovery Due to Lowered `rsETHPrice` Baseline - (File: contracts/LRTOracle.sol)

## Summary
In `LRTOracle._updateRsETHPrice()`, the fee baseline is computed from `rsETHPrice`, which is unconditionally updated to the latest price on every call — including price decreases. When the price subsequently recovers, the fee calculation treats the recovery as new yield and mints rsETH to the treasury, diluting all existing rsETH holders even though no genuine new gains occurred.

## Finding Description
The fee baseline at line 234 uses the stored `rsETHPrice`:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);   // L234
```

At line 313, `rsETHPrice` is unconditionally overwritten with the new price, even when it is lower:

```solidity
rsETHPrice = newRsETHPrice;   // L313
```

The contract maintains `highestRsethPrice` as a true high-water mark (updated only upward at L294–296), but it is used exclusively for the price-threshold guard (L252–267) and the downside-protection pause (L270–282) — never for the fee baseline.

The downside-protection pause at L270–282 only triggers when `pricePercentageLimit > 0` AND the drop exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`. If `pricePercentageLimit == 0` (disabled) or the drop is within the configured limit, the protocol continues operating: `rsETHPrice` is lowered, and on recovery, `previousTVL` is computed from the lowered baseline, causing fees to be charged on the full recovery amount.

**Exploit path:**
1. Price rises from 1.0 → 1.2; fee correctly charged on 0.2 gain. `rsETHPrice = 1.2`, `highestRsethPrice = 1.2`.
2. Price drops to 0.9 (within `pricePercentageLimit` or limit is 0); no fee (TVL decreased). `rsETHPrice = 0.9`, `highestRsethPrice = 1.2`.
3. Any external caller invokes `updateRSETHPrice()` (public, no role restriction, L87) when price recovers to 1.2. `previousTVL = supply * 0.9`. `totalETHInProtocol = supply * 1.2`. `rewardAmount = supply * 0.3`. Fee is minted on 0.3 of pure principal recovery.

The daily fee mint limit (`maxFeeMintAmountPerDay`, L205) caps the per-day damage but does not prevent the incorrect fee from being charged when set to any non-zero operational value.

## Impact Explanation
**High — Theft of unclaimed yield.** The protocol mints rsETH to the treasury on recovery of losses. This dilutes all rsETH holders proportionally: their share of the underlying ETH is reduced by the incorrectly minted fee tokens. The effect compounds on every dip-and-recovery cycle, scaling with the size of the dip and total rsETH supply.

## Likelihood Explanation
**Medium.** LST prices (stETH, cbETH, rETH) fluctuate relative to ETH due to secondary-market discounts, slashing events, and oracle lag. A dip followed by recovery is a routine market event. `updateRSETHPrice()` is public and permissionless (L87), requiring no privileged actor. The scenario is fully reachable whenever `pricePercentageLimit` is zero or the price drop is within the configured limit.

## Recommendation
Replace the fee baseline with `highestRsethPrice` instead of `rsETHPrice`:

```solidity
// Use highestRsethPrice as the fee baseline so recovery of losses is never charged
uint256 previousTVL = rsethSupply.mulWad(highestRsethPrice);
```

This ensures fees are only charged on TVL that genuinely exceeds the previous all-time high, exactly mirroring the intent of `highestRsethPrice`.

## Proof of Concept
Foundry fork/unit test sequence:

1. Deploy with 1000 stETH; oracle price = 1.0 → `rsETHPrice = 1.0`, `highestRsethPrice = 1.0`.
2. Set oracle price = 1.2; call `updateRSETHPrice()`. Assert fee minted on 200 ETH of gain. Assert `rsETHPrice = 1.2`, `highestRsethPrice = 1.2`.
3. Set oracle price = 0.9 (within `pricePercentageLimit` or set limit to 0); call `updateRSETHPrice()`. Assert no fee minted. Assert `rsETHPrice = 0.9`, `highestRsethPrice = 1.2`.
4. Set oracle price = 1.2; call `updateRSETHPrice()` from an unprivileged address. Assert `protocolFeeInETH > 0` and rsETH minted to treasury on `rewardAmount = supply * 0.3`. Assert treasury rsETH balance increased — confirming fee charged on pure principal recovery. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
