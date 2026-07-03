Looking at the full `_updateRsETHPrice()` logic and the deposit flow, I need to trace the exact path and guards.

### Title
Stale rsETH Price Window After Large EigenLayer Checkpoint Allows Yield Theft — (`contracts/LRTOracle.sol`)

### Summary

When a large EigenLayer checkpoint credits consensus rewards that push `newRsETHPrice` above `highestRsethPrice + pricePercentageLimit * highestRsethPrice`, the public `updateRSETHPrice()` reverts for all non-manager callers. The deposit pool has no staleness guard and continues to mint rsETH at the old, lower `rsETHPrice`. An attacker can deposit during this window and receive more rsETH than the current TVL justifies, diluting existing holders' yield.

---

### Finding Description

`_updateRsETHPrice()` contains an upside circuit-breaker: [1](#0-0) 

If `newRsETHPrice` exceeds `highestRsethPrice` by more than `pricePercentageLimit`, the function reverts for any caller that lacks the `MANAGER` role. The public entry point `updateRSETHPrice()` is therefore blocked: [2](#0-1) 

Only the manager can bypass this via `updateRSETHPriceAsManager()`: [3](#0-2) 

During the window between the checkpoint completing and the manager calling `updateRSETHPriceAsManager()`, the stored `rsETHPrice` is stale (lower than actual). The deposit pool mints rsETH using this stale price with no freshness check: [4](#0-3) 

`lrtOracle.rsETHPrice()` is the last written value — it is not recomputed on read. There is no oracle staleness guard, no pause triggered by the revert, and no minimum-price-freshness requirement anywhere in `_beforeDeposit()`: [5](#0-4) 

The checkpoint mechanism that causes the price jump is `getEffectivePodShares()`, which reflects EigenLayer's `podOwnerDepositShares` immediately after a checkpoint proof is submitted: [6](#0-5) 

This value feeds directly into `_getTotalEthInProtocol()` and therefore into `newRsETHPrice`.

---

### Impact Explanation

An attacker who deposits `X` ETH at the stale price `P_old` receives `X / P_old` rsETH. After the manager updates the price to `P_new > P_old`, the attacker's rsETH is redeemable for `(X / P_old) * P_new > X` ETH. The excess `(X / P_old) * (P_new - P_old)` is extracted from the yield that rightfully belongs to existing holders — **theft of unclaimed yield (High)**.

---

### Likelihood Explanation

- `pricePercentageLimit` is explicitly designed to be set to a small value (the comment documents 1% = `1e16`). This is the intended operational configuration, not a misconfiguration.
- EigenLayer checkpoints accumulate consensus rewards over time. If checkpoints are infrequent (days or weeks), a single checkpoint can credit far more than 1% yield in one step.
- The attacker needs no special role, no front-running, and no collusion. They only need to observe that `updateRSETHPrice()` reverts (trivially detectable on-chain) and then call `depositETH()` or `depositAsset()` before the manager responds.
- The manager response latency (even minutes) is sufficient for the attack.

---

### Recommendation

1. **Freshness gate on deposits**: Record the block number or timestamp of the last successful `rsETHPrice` update and revert deposits if the price is older than a configurable threshold.
2. **Auto-pause on blocked update**: When `isPriceIncreaseOffLimit` is true and the caller is not a manager, pause the deposit pool in addition to reverting, so no deposits can occur at the stale price.
3. **Partial-update approach**: Instead of reverting, cap the price update to `highestRsethPrice * (1 + pricePercentageLimit)` for public callers, allowing the price to advance in increments without creating a stale window.

---

### Proof of Concept

```solidity
// Fork test (local fork, no mainnet)
// Preconditions:
//   pricePercentageLimit = 1e16 (1%)
//   Protocol has validators; checkpoint not run for 2 weeks
//   newRsETHPrice would be ~3% above highestRsethPrice after checkpoint

// Step 1: Complete EigenLayer checkpoint proof (operator action, not attacker)
nodeDelegator.verifyCheckpointProofs(...);
// getEffectivePodShares() now returns a higher value

// Step 2: Confirm public update is blocked
vm.expectRevert(ILRTOracle.PriceAboveDailyThreshold.selector);
lrtOracle.updateRSETHPrice(); // reverts for non-manager

// Step 3: Attacker deposits at stale price
uint256 staleMintAmount = lrtDepositPool.getRsETHAmountToMint(ETH_TOKEN, 100 ether);
// staleMintAmount > what 100 ETH should buy at the true price

vm.prank(attacker);
lrtDepositPool.depositETH{value: 100 ether}(0, "");

// Step 4: Manager updates price
vm.prank(manager);
lrtOracle.updateRSETHPriceAsManager();

// Step 5: Assert attacker profit
uint256 attackerRsETHValue = rsETH.balanceOf(attacker) * lrtOracle.rsETHPrice() / 1e18;
assertGt(attackerRsETHValue, 100 ether); // attacker received more than deposited
```

### Citations

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

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
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

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-669)
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
```

**File:** contracts/NodeDelegator.sol (L556-562)
```text
    function getEffectivePodShares() external view override returns (uint256 ethStaked) {
        uint256 withdrawableShare =
            NodeDelegatorHelper.getWithdrawableShare(lrtConfig, IStrategy(lrtConfig.beaconChainETHStrategy()));

        // staker balances can no longer be negative
        return stakedButUnverifiedNativeETH + withdrawableShare;
    }
```
