### Title
`maxFeeMintAmountPerDay = 0` Permanently Blocks `rsETHPrice` Updates During Active Protocol Operation — (`contracts/LRTOracle.sol`)

---

### Summary

When `maxFeeMintAmountPerDay` is zero (its Solidity default, and a value the manager can set), any call to `_updateRsETHPrice()` reverts whenever the protocol is active and TVL has increased. The paused-protocol path is unaffected because it bypasses fee calculation entirely. This creates a permanent asymmetry: a paused protocol with growing TVL can still update its price, but an active protocol with growing TVL cannot.

---

### Finding Description

In `_updateRsETHPrice()`, the fee path branches on `protocolPaused`: [1](#0-0) 

When `protocolPaused = false` and `totalETHInProtocol > previousTVL`, `protocolFeeInETH` is set to a positive value. The function then computes `rsethAmountToMintAsProtocolFee` and calls: [2](#0-1) 

Inside `_checkAndUpdateDailyFeeMintLimit`, the guard is: [3](#0-2) 

When `maxFeeMintAmountPerDay = 0` and `feeAmount > 0`, the condition `0 + feeAmount > 0` is always `true`, so the function reverts with `DailyFeeMintLimitExceeded`. Execution never reaches: [4](#0-3) 

**Contrast with the paused path:** when `protocolPaused = true`, `protocolFeeInETH` stays `0`, the `else` branch calls `_checkAndUpdateDailyFeeMintLimit(0)`, the check `0 > 0` is false, and `rsETHPrice` is updated successfully.

`maxFeeMintAmountPerDay` is a plain `uint256` storage variable with no initialization in either `initialize` or `reinitialize`: [5](#0-4) [6](#0-5) 

Its Solidity default is `0`. It can only be changed by `onlyLRTManager` via `setMaxFeeMintAmountPerDay`: [7](#0-6) 

Until that call is made — or if the manager deliberately sets it back to `0` — every `updateRSETHPrice()` call reverts the moment TVL grows while the protocol is active.

The cross-chain rate provider reads `rsETHPrice` directly: [8](#0-7) 

A stale `rsETHPrice` propagates to all cross-chain consumers as well.

---

### Impact Explanation

`rsETHPrice` is frozen at its last value. All accrued yield (the TVL increase) is invisible to rsETH holders: they cannot realize it through redemption, and the cross-chain rate provider broadcasts a stale rate. This matches **Medium — Permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

- `maxFeeMintAmountPerDay` is `0` by default and is not set during `initialize` or `reinitialize`. The window between deployment/upgrade and the manager's first `setMaxFeeMintAmountPerDay` call is a guaranteed trigger.
- The manager can also set it to `0` intentionally (e.g., to "pause" fee minting) without realizing it also freezes price updates.
- No privileged compromise is required; the broken state is the contract's own default.

---

### Recommendation

Add a zero-value bypass in `_checkAndUpdateDailyFeeMintLimit` so that `maxFeeMintAmountPerDay = 0` means "no cap" (or "fee minting disabled but price still updates"), consistent with how `remainingDailyFeeMintLimit()` already treats it:

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
    if (feeAmount == 0) return; // nothing to check
    if (maxFeeMintAmountPerDay == 0) revert FeeMintingDisabled(); // explicit, or skip fee entirely upstream

    if (block.timestamp >= feePeriodStartTime + 1 days) { ... }
    if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) { revert ...; }
    currentPeriodMintedFeeAmount += feeAmount;
}
```

Alternatively, treat `maxFeeMintAmountPerDay = 0` as "uncapped" and remove the revert path for that case. Either way, ensure `rsETHPrice` can always advance when TVL grows, regardless of the fee cap setting.

Also initialize `maxFeeMintAmountPerDay` to a sensible non-zero value in `reinitialize` to eliminate the default-zero window.

---

### Proof of Concept

```solidity
// Differential test: same TVL increase, different pause state + maxFeeMintAmountPerDay=0

function test_asymmetry() public {
    // Setup: maxFeeMintAmountPerDay = 0 (default, never set)
    // rsETHPrice = 1e18, rsethSupply > 0, TVL = 100 ETH

    // Case A: pause depositPool → protocolPaused = true
    depositPool.pause();
    oracle.updateRSETHPrice(); // succeeds, rsETHPrice advances
    assertGt(oracle.rsETHPrice(), 1e18);

    // Reset price
    // ...

    // Case B: unpause → protocolPaused = false, same TVL increase
    depositPool.unpause();
    vm.expectRevert(
        abi.encodeWithSelector(ILRTOracle.DailyFeeMintLimitExceeded.selector, feeAmount, 0)
    );
    oracle.updateRSETHPrice(); // reverts — rsETHPrice stays stale
}
```

`(a)` succeeds and `(b)` reverts, confirming the invariant break: `rsETHPrice` must advance with TVL regardless of fee cap configuration.

### Citations

**File:** contracts/LRTOracle.sol (L35-35)
```text
    uint256 public maxFeeMintAmountPerDay;
```

**File:** contracts/LRTOracle.sol (L72-79)
```text
    function reinitialize(uint256 _feePeriodStartTime) external reinitializer(2) onlyLRTManager {
        if (_feePeriodStartTime > block.timestamp || _feePeriodStartTime <= block.timestamp - 1 days) {
            revert PeriodStartTimeShouldBeWithin24Hours();
        }

        feePeriodStartTime = _feePeriodStartTime;
        emit FeePeriodStartTimeSet(_feePeriodStartTime);
    }
```

**File:** contracts/LRTOracle.sol (L132-135)
```text
    function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
        maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
        emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
    }
```

**File:** contracts/LRTOracle.sol (L205-207)
```text
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }
```

**File:** contracts/LRTOracle.sol (L240-247)
```text
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-311)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-29)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```
