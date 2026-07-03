The code confirms the claim. Let me verify the key details:

- `_updateRsETHPrice()` at line 263-264: reverts with `PriceAboveDailyThreshold()` for non-managers when price increase exceeds limit — no pause issued. [1](#0-0) 
- Price-decrease branch at lines 277-281: pauses `lrtDepositPool`, `withdrawalManager`, and the oracle before returning. [2](#0-1) 
- `rsETHPrice = newRsETHPrice` at line 313 is never reached on the revert path. [3](#0-2) 
- `getRsETHAmountToMint` divides by the stale `lrtOracle.rsETHPrice()`. [4](#0-3) 
- `updateRSETHPrice()` is public and permissionless. [5](#0-4) 

All code references check out. The finding is valid.

---

Audit Report

## Title
Missing Deposit Pool Pause on Price-Increase Threshold Revert Leaves `rsETHPrice` Stale, Enabling Excess rsETH Minting — (File: `contracts/LRTOracle.sol`)

## Summary
When `LRTOracle.updateRSETHPrice()` is called by a non-manager and the computed price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, the function reverts with `PriceAboveDailyThreshold()` without pausing `LRTDepositPool`. The stored `rsETHPrice` remains at the old lower value. Because `LRTDepositPool.getRsETHAmountToMint()` divides by the stale `rsETHPrice`, depositors receive more rsETH than the current TVL justifies, diluting all existing rsETH holders' unclaimed yield.

## Finding Description
Inside `_updateRsETHPrice()`, the price-increase guard (lines 252–267 of `contracts/LRTOracle.sol`) checks whether the new price exceeds `highestRsethPrice` by more than `pricePercentageLimit`. If `isPriceIncreaseOffLimit` is true and the caller lacks `MANAGER` role, the function reverts immediately:

```solidity
if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
    revert PriceAboveDailyThreshold();
}
```

Execution never reaches `rsETHPrice = newRsETHPrice` at line 313, so `rsETHPrice` stays at the old, lower value.

By contrast, the price-decrease branch (lines 270–282) explicitly pauses `lrtDepositPool`, `withdrawalManager`, and the oracle itself before returning:

```solidity
if (!lrtDepositPool.paused()) lrtDepositPool.pause();
if (!withdrawalManager.paused()) withdrawalManager.pause();
_pause();
return;
```

No equivalent protection exists for the price-increase revert path. `LRTDepositPool` remains open and its mint calculation at line 520 reads the stale value:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Because `rsETHPrice` is lower than the true current price, the denominator is too small and every depositor receives more rsETH than the protocol's TVL supports.

The trigger is fully permissionless: `updateRSETHPrice()` is `public` with no role restriction, so any external account can call it to force the revert condition whenever accumulated rewards push the price above the configured threshold.

## Impact Explanation
**High — Theft of unclaimed yield.**

Staking rewards that have accrued to the protocol (reflected in the higher TVL) are not yet captured in `rsETHPrice`. When a depositor deposits during the stale-price window, they receive rsETH priced at the old rate. Once a manager calls `updateRSETHPriceAsManager()` and the price is corrected upward, the attacker's tokens are immediately worth more ETH than was deposited. The excess value is extracted directly from the proportional claims of prior rsETH holders, whose share of the TVL is diluted. This matches the allowed impact: **High. Theft of unclaimed yield.**

## Likelihood Explanation
**Medium.**

- `updateRSETHPrice()` is public; any account can trigger the revert condition at zero cost.
- The condition fires whenever accumulated rewards push the price above `pricePercentageLimit` — a routine occurrence in a live restaking protocol.
- The window between the revert and a manager's corrective `updateRSETHPriceAsManager()` call can span hours, during which the deposit pool is fully open.
- No special permissions or capital beyond a normal deposit are required.
- The attacker can repeat the attack every time rewards accumulate past the threshold before a manager acts.

## Recommendation
Mirror the price-decrease branch's behavior for the price-increase case. When `isPriceIncreaseOffLimit` is true and the caller is not a manager, pause `lrtDepositPool` (and optionally `withdrawalManager`) before reverting:

```solidity
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        revert PriceAboveDailyThreshold();
    }
}
```

Alternatively, cap the price update at `highestRsethPrice * (1 + pricePercentageLimit)` for non-manager callers so that `rsETHPrice` is never left stale while deposits remain open.

## Proof of Concept
1. Deploy with `rsETHPrice = 1.00e18`, `highestRsethPrice = 1.00e18`, `pricePercentageLimit = 1e16` (1%).
2. Staking rewards accrue; `_getTotalEthInProtocol()` now implies `newRsETHPrice = 1.02e18` (2% increase).
3. Any account calls `LRTOracle.updateRSETHPrice()`. Inside `_updateRsETHPrice()`, `isPriceIncreaseOffLimit = true` and the caller lacks `MANAGER` role → `revert PriceAboveDailyThreshold()`. `rsETHPrice` remains `1.00e18`. No pause is issued.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
5. `getRsETHAmountToMint` computes `(100e18 * 1e18) / 1.00e18 = 100e18` rsETH. Fair amount is `(100e18 * 1e18) / 1.02e18 ≈ 98.04e18` rsETH. Attacker receives ~1.96 rsETH in excess.
6. Manager calls `updateRSETHPriceAsManager()`. `rsETHPrice` updates to `1.02e18`. Attacker's 100 rsETH is now redeemable for 102 ETH — 2 ETH extracted from prior holders.

**Foundry test plan:** Fork mainnet, set `pricePercentageLimit = 1e16`, simulate reward accrual by mocking `_getTotalEthInProtocol()` to return a 2% higher value, call `updateRSETHPrice()` from a non-manager EOA (assert revert), then call `depositAsset` and assert the minted rsETH exceeds the fair amount. Finally call `updateRSETHPriceAsManager()` and assert the attacker's rsETH redemption value exceeds their deposit.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTOracle.sol (L277-281)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
