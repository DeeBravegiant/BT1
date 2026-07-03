Audit Report

## Title
Unpermissioned `sendFunds()` Enables Deposit at Stale Oracle Price to Steal MEV Yield — (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

## Summary
`FeeReceiver.sendFunds()` carries no access control, allowing any caller to flush accumulated MEV/EL rewards into `LRTDepositPool`. This immediately raises the deposit pool's ETH balance and therefore the computed TVL, but the stored `rsETHPrice` in `LRTOracle` remains stale. If the flushed amount is large enough to push the computed price above `pricePercentageLimit`, the public `updateRSETHPrice()` reverts for non-managers, locking the oracle. An attacker can then deposit at the artificially low stale price, receiving more rsETH than the actual backing warrants, and capture a disproportionate share of the flushed MEV yield at the expense of existing holders.

## Finding Description

**Root cause — unpermissioned reward flush:**

`FeeReceiver.sendFunds()` has no role guard: [1](#0-0) 

`receiveFromRewardReceiver()` is equally unguarded: [2](#0-1) 

**ETH balance immediately reflected in TVL:**

`getETHDistributionData()` uses the raw balance of the deposit pool: [3](#0-2) 

`_getTotalEthInProtocol()` calls `getTotalAssetDeposits(ETH)` → `getAssetDistributionData(ETH)` → `getETHDistributionData()`, so flushed ETH is immediately part of `totalETHInProtocol`: [4](#0-3) 

**Oracle update reverts for non-managers when price exceeds threshold:**

`updateRSETHPrice()` is public with no role check: [5](#0-4) 

Inside `_updateRsETHPrice()`, if the computed price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, any non-manager caller reverts: [6](#0-5) 

The price is **not written** when the function reverts — `rsETHPrice = newRsETHPrice` is only reached after the threshold check passes: [7](#0-6) 

**Attacker deposits at stale (below-actual) price:**

`getRsETHAmountToMint` divides by the stored stale `rsETHPrice`: [8](#0-7) 

Because `rsETHPrice` has not been updated to reflect the flushed MEV rewards, the attacker receives more rsETH than the actual backing warrants. When the manager eventually calls `updateRSETHPriceAsManager()`, the price jumps to reflect the flushed rewards, and the attacker's over-minted rsETH is now worth more than they paid — the difference is extracted directly from existing holders' yield.

**Existing guards are insufficient:**

- `_beforeDeposit` only checks deposit limits and a minimum rsETH slippage parameter (`minRSETHAmountExpected`), which the attacker sets to 0.
- The `pricePercentageLimit` guard is intended to protect against oracle manipulation, but here it is weaponized: it prevents the price from being updated by anyone except the manager, extending the window during which the stale price is exploitable.
- There is no check in `depositETH` or `receiveFromRewardReceiver` that forces an oracle update before minting.

## Impact Explanation

**High — Theft of unclaimed yield.** MEV/EL rewards accumulated in `FeeReceiver` represent yield owed to existing rsETH holders (they raise the per-token ETH backing). By flushing those rewards and locking the oracle, an attacker deposits at the pre-flush price and claims a disproportionate share of that yield when the manager updates the price. Existing holders' rsETH is diluted by exactly the amount the attacker over-minted. The loss is quantifiable: `attacker_profit ≈ (flushed_MEV / totalSupply) * attacker_deposit / rsETHPrice_stale`.

## Likelihood Explanation

- Both `sendFunds()` and `updateRSETHPrice()` are permissionless and callable in consecutive transactions by any EOA or contract.
- MEV rewards naturally accumulate between keeper calls; a single large batch (e.g., after a missed keeper run or a high-MEV block sequence) can exceed `pricePercentageLimit` in one flush.
- The attacker needs no privileged access and no capital beyond the deposit amount.
- The attack window lasts until the manager notices and calls `updateRSETHPriceAsManager()`, which may be minutes to hours depending on monitoring.
- The attack is repeatable every time rewards accumulate above the threshold.

## Recommendation

1. **Add access control to `sendFunds()`** — restrict it to `MANAGER` or a dedicated keeper role so that the timing of reward flushes is controlled and cannot be weaponized by an unprivileged caller.
2. **Or atomically update the oracle in the same transaction as the flush** — require `sendFunds()` to call `_updateRsETHPrice()` (manager-gated) so the price is always current when rewards land.
3. **Or cap the single-flush amount** — allow partial flushes so that no single call can push the price above the threshold.
4. **Add access control to `receiveFromRewardReceiver()`** — restrict it to the known `FeeReceiver` address to prevent arbitrary ETH injection into the deposit pool's balance.

## Proof of Concept

```solidity
// Foundry fork test
function testYieldTheft() public {
    // Precondition: FeeReceiver holds accumulated MEV rewards
    // large enough to push rsETH price > pricePercentageLimit above highestRsethPrice
    vm.deal(address(feeReceiver), LARGE_MEV_AMOUNT);

    uint256 priceBefore = oracle.rsETHPrice();

    // Step 1: Any caller flushes MEV rewards — permissionless
    feeReceiver.sendFunds();
    // Deposit pool balance now includes LARGE_MEV_AMOUNT; oracle price is stale

    // Step 2: Attacker deposits at stale (below-actual) price
    vm.startPrank(attacker);
    vm.deal(attacker, 10 ether);
    pool.depositETH{value: 10 ether}(0, "");
    // Attacker receives more rsETH than actual backing warrants

    // Step 3: Non-manager cannot update oracle — price is locked above threshold
    vm.expectRevert(LRTOracle.PriceAboveDailyThreshold.selector);
    oracle.updateRSETHPrice();
    vm.stopPrank();

    // Step 4: Manager unlocks oracle
    vm.prank(manager);
    oracle.updateRSETHPriceAsManager();

    uint256 priceAfter = oracle.rsETHPrice();
    assertGt(priceAfter, priceBefore);

    // Attacker's rsETH is now worth more than 10 ether — yield stolen from existing holders
    uint256 attackerValue = rseth.balanceOf(attacker) * priceAfter / 1e18;
    assertGt(attackerValue, 10 ether);
}
```

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTOracle.sol (L331-343)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
