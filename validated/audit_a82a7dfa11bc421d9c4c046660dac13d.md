Audit Report

## Title
Unpermissioned `sendFunds()` Enables Oracle Price-Threshold Lock, Allowing Deposits at Stale Price to Steal MEV Yield — (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

## Summary
`FeeReceiver.sendFunds()` carries no access control, allowing any caller to flush accumulated MEV/EL rewards into `LRTDepositPool` at will. Because `getETHDistributionData()` counts `address(this).balance` directly, the deposit pool's ETH balance and the oracle's computed TVL rise immediately. If the flushed amount is large enough to push the computed rsETH price above `pricePercentageLimit`, every subsequent public call to `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold()`, leaving `rsETHPrice` stale. An attacker can then deposit at the artificially low price and capture a disproportionate share of the flushed MEV yield.

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

Only the manager can escape via `updateRSETHPriceAsManager()`: [8](#0-7) 

**Attacker deposits at stale (below-actual) price:**

`getRsETHAmountToMint` divides by the stale `rsETHPrice`: [9](#0-8) 

With `rsETHPrice` frozen below the true backing, the attacker receives more rsETH than the actual backing warrants. When the manager eventually calls `updateRSETHPriceAsManager()`, the price jumps to reflect the flushed rewards, and the attacker's over-minted rsETH is now worth more than they deposited — at the expense of existing holders whose per-token backing was diluted.

## Impact Explanation

The MEV/EL rewards accumulated in `FeeReceiver` represent unclaimed yield owed to existing rsETH holders (they raise the per-token backing). By flushing those rewards and simultaneously locking the oracle for non-managers, an attacker can deposit at the pre-flush price and claim a disproportionate share of that yield when the manager eventually updates the price. This is a direct, quantifiable **theft of unclaimed yield** (High severity per the allowed impact scope).

## Likelihood Explanation

- Both `sendFunds()` and `updateRSETHPrice()` are permissionless and callable in consecutive transactions by any EOA.
- MEV rewards naturally accumulate between keeper calls; a single large batch (e.g., after a missed keeper run or a high-MEV block sequence) can exceed `pricePercentageLimit` in one flush.
- The attacker requires no privileged access and only needs capital for the deposit.
- The attack window lasts until the manager notices and calls `updateRSETHPriceAsManager()`, which may be minutes to hours depending on monitoring.
- The condition `pricePercentageLimit > 0` must hold, but this is the expected production configuration.

## Recommendation

1. **Add access control to `sendFunds()`** — restrict it to `MANAGER` or a dedicated keeper role so that the timing of reward flushes is controlled.
2. **Or batch the flush with the oracle update** — require that `sendFunds()` atomically calls `updateRSETHPrice()` (manager-gated) in the same transaction, so the price is always updated when rewards are flushed.
3. **Alternatively, cap the single-flush amount** — allow partial flushes so that no single call can push the price above the threshold.

## Proof of Concept

```solidity
// Foundry fork test
function testOracleLockYieldTheft() public {
    // Precondition: FeeReceiver holds enough ETH to push price > pricePercentageLimit
    vm.deal(address(feeReceiver), LARGE_MEV_AMOUNT);

    uint256 priceBefore = oracle.rsETHPrice();

    // Step 1: any address flushes MEV rewards — permissionless
    feeReceiver.sendFunds();

    // Step 2: attacker deposits at stale price (oracle not yet updated)
    vm.startPrank(attacker);
    vm.deal(attacker, 10 ether);
    pool.depositETH{value: 10 ether}(0, "");

    // Step 3: oracle is locked for non-managers
    vm.expectRevert(LRTOracle.PriceAboveDailyThreshold.selector);
    oracle.updateRSETHPrice();
    vm.stopPrank();

    // Step 4: manager unlocks
    vm.prank(manager);
    oracle.updateRSETHPriceAsManager();

    uint256 priceAfter = oracle.rsETHPrice();
    assertGt(priceAfter, priceBefore);

    // Attacker's rsETH is now worth more than 10 ETH deposited
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

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-266)
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
