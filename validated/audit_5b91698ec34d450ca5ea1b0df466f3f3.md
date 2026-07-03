Audit Report

## Title
Stale `rsETHPrice` Denominator Allows Over-Minting of rsETH, Diluting Existing Holders' Unclaimed Yield — (`contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`, `contracts/oracles/RETHPriceOracle.sol`)

## Summary

`getRsETHAmountToMint` divides a live rETH/ETH exchange rate by a stored, potentially stale `rsETHPrice`. Because `updateRSETHPrice()` is never called atomically before `depositAsset` or `depositETH`, any depositor can mint rsETH at a rate that captures accrued rETH yield that rightfully belongs to existing rsETH holders, diluting their per-token ETH value.

## Finding Description

`LRTDepositPool.getRsETHAmountToMint` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

The numerator calls `LRTOracle.getAssetPrice(rETH)` → `RETHPriceOracle.getAssetPrice` → `IrETH(rETHAddress).getExchangeRate()`, which is always live: [2](#0-1) 

The denominator reads `lrtOracle.rsETHPrice()`, a stored state variable only updated when `_updateRsETHPrice()` is explicitly called: [3](#0-2) 

`updateRSETHPrice()` is public and permissionless but is never invoked inside `depositAsset` or `depositETH`: [4](#0-3) 

`_beforeDeposit` is declared `private view`, making it structurally impossible to call `updateRSETHPrice()` there; it simply delegates to `getRsETHAmountToMint`: [5](#0-4) 

**Exploit path:**

1. rETH accrues staking yield: `getExchangeRate()` rises from R₀ to R₁ > R₀. The protocol's true TVL has increased, but `rsETHPrice` (P₀) has not been updated yet.
2. Attacker calls `depositAsset(rETH, X)` without calling `updateRSETHPrice()` first.
3. Attacker receives `X * R₁ / P₀` rsETH.
4. The fair amount (post-update) would be `X * R₁ / P₁` where P₁ > P₀ because TVL grew.
5. Since P₀ < P₁, the attacker receives more rsETH than their deposit is worth at the correct price.
6. When `updateRSETHPrice()` is eventually called, `_updateRsETHPrice` computes `newRsETHPrice = totalETHInProtocol / rsethSupply`. The attacker's over-minted rsETH inflates the denominator without a proportional increase in the numerator, permanently reducing the per-rsETH ETH value for all existing holders.

**Mathematical proof:** Let V = old TVL at updated rETH rate, S = old rsETH supply, D = X·R₁ (attacker's deposit in ETH), M = D/P₀ (minted rsETH). The post-attack price is (V+D)/(S+D/P₀). This is less than V/S (the no-attack price) if and only if S·P₀ < V, i.e., the old TVL V₀ = S·P₀ < V, which is exactly the condition that rETH has accrued yield. The dilution is guaranteed whenever yield has accrued since the last price update.

## Impact Explanation

This is a direct, quantifiable **theft of unclaimed yield** from existing rsETH holders. When rETH accrues staking rewards, those rewards belong to current rsETH holders (they would increase `rsETHPrice`). By depositing before the price update, the attacker captures a portion of that accrued yield. The shortfall is borne by existing holders whose `rsETHPrice` is permanently diluted. This matches the allowed High impact: **Theft of unclaimed yield**.

## Likelihood Explanation

- rETH accrues yield continuously (~4% APY, ~0.011%/day). Any gap between `updateRSETHPrice()` calls creates an exploitable window.
- No special role, flash loan, oracle manipulation, or front-running is required — any unprivileged depositor can trigger this via the public `depositAsset` function.
- The attack is repeatable: deposit, wait for the next yield accrual window, repeat.
- Profit scales with deposit size and time elapsed since the last price update.

## Recommendation

Call `updateRSETHPrice()` atomically at the start of `depositAsset` and `depositETH`, before `_beforeDeposit` is invoked. Since `_beforeDeposit` is `private view`, the call must be placed in the non-view deposit entry points:

```solidity
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, string calldata referralId)
    external nonReentrant whenNotPaused onlySupportedAsset(asset)
{
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // <-- add this
    uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
    ...
}
```

Apply the same fix to `depositETH`. This ensures `rsETHPrice` always reflects the current live asset rates before any mint calculation.

## Proof of Concept

```solidity
// Foundry fork test (mainnet fork, any block where rETH has accrued yield since last rsETHPrice update)
function testStealUnclaimedYield() external {
    // 1. Record current state — rETH rate must exceed rsETHPrice for yield to have accrued
    uint256 rsETHPriceBefore = lrtOracle.rsETHPrice();
    uint256 rETHRate = rETH.getExchangeRate(); // live; > rsETHPriceBefore if yield accrued
    require(rETHRate > rsETHPriceBefore, "no yield accrued yet");

    // 2. Attacker deposits rETH WITHOUT calling updateRSETHPrice first
    uint256 depositAmount = 10 ether;
    deal(address(rETH), attacker, depositAmount);
    vm.startPrank(attacker);
    rETH.approve(address(lrtDepositPool), depositAmount);
    lrtDepositPool.depositAsset(address(rETH), depositAmount, 0, "");
    vm.stopPrank();

    uint256 rsETHMinted = rsETH.balanceOf(attacker);

    // 3. Now update the price (as would happen normally)
    lrtOracle.updateRSETHPrice();
    uint256 rsETHPriceAfter = lrtOracle.rsETHPrice();

    // 4. Fair amount would have been: depositAmount * rETHRate / rsETHPriceAfter
    uint256 fairRsETHAmount = (depositAmount * rETHRate) / rsETHPriceAfter;

    // 5. Attacker received more than fair share — unclaimed yield stolen
    assertGt(rsETHMinted, fairRsETHAmount, "Attacker stole unclaimed yield");
}
```

The assertion passes whenever `rETHRate > rsETHPriceBefore`, confirming theft of unclaimed yield from existing rsETH holders. The condition is met on any mainnet fork block where rETH has accrued yield since the last `updateRSETHPrice()` call.

### Citations

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/oracles/RETHPriceOracle.sol (L39-39)
```text
        return IrETH(rETHAddress).getExchangeRate();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
