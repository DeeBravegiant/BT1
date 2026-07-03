### Title
`updateRSETHPrice()` Permanently Blocked When `maxFeeMintAmountPerDay` Is Zero and Protocol Fee Accrues — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._checkAndUpdateDailyFeeMintLimit()` lacks a zero-check on `maxFeeMintAmountPerDay`. When this variable is at its default value of `0` and a non-zero protocol fee is computed, the function unconditionally reverts, permanently blocking both the public `updateRSETHPrice()` and the manager-only `updateRSETHPriceAsManager()` until an admin action corrects the state. This is a direct analog to the GoGoPool M-21 pattern: a missing zero-guard on a count/limit causes a revert that blocks a critical public function.

---

### Finding Description

`LRTOracle` tracks a daily cap on protocol-fee rsETH minting via `maxFeeMintAmountPerDay`. This variable is a plain storage slot initialized to `0` by default. The manager can set it via `setMaxFeeMintAmountPerDay()`, but there is no requirement that it be set before the oracle is used.

The internal function `_checkAndUpdateDailyFeeMintLimit()` enforces the cap: [1](#0-0) 

```solidity
// Check if minting would exceed the daily limit
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
}
```

When `maxFeeMintAmountPerDay == 0` and `feeAmount > 0`, the condition `feeAmount > 0` is trivially true, so the function always reverts.

`_checkAndUpdateDailyFeeMintLimit` is called unconditionally at the end of every `_updateRsETHPrice()` execution: [2](#0-1) 

```solidity
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
    ...
} else {
    _checkAndUpdateDailyFeeMintLimit(0);   // safe path
}
```

`protocolFeeInETH > 0` whenever `protocolFeeInBPS > 0` (set in `LRTConfig`) and `totalETHInProtocol > previousTVL` (TVL grew). Both conditions are normal operating states.

The public entry point is: [3](#0-2) 

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

The manager-only entry point suffers identically: [4](#0-3) 

```solidity
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
```

Notably, the view helper `remainingDailyFeeMintLimit()` already guards against `maxFeeMintAmountPerDay == 0`, confirming the developers intended this to be a valid "unset" state: [5](#0-4) 

```solidity
function remainingDailyFeeMintLimit() external view returns (uint256) {
    if (maxFeeMintAmountPerDay == 0) return 0;
```

But `_checkAndUpdateDailyFeeMintLimit()` has no equivalent guard, creating an inconsistency that causes a revert.

---

### Impact Explanation

When `maxFeeMintAmountPerDay == 0` and TVL grows with `protocolFeeInBPS > 0`:

1. **`updateRSETHPrice()` is completely blocked** — no caller (public or manager) can update the rsETH price.
2. **Protocol fee rsETH is never minted** — accrued yield owed to the treasury is permanently frozen for the duration of the blockage.
3. **rsETH price becomes stale** — deposits via `depositETH()`/`depositAsset()` and withdrawals via `initiateWithdrawal()` all read `lrtOracle.rsETHPrice()`, which is now frozen at its last value, enabling arbitrage against the protocol.

The blockage persists until the manager calls `setMaxFeeMintAmountPerDay()` with a non-zero value. Until then, unclaimed yield (protocol fee) is frozen and the price oracle is non-functional.

**Impact classification**: Medium — Permanent freezing of unclaimed yield; temporary freezing of the price-update mechanism.

---

### Likelihood Explanation

The scenario is realistic during normal protocol operation:

- `maxFeeMintAmountPerDay` defaults to `0` and requires an explicit manager action to set.
- `protocolFeeInBPS` is a separate config parameter set by the admin.
- If the admin sets `protocolFeeInBPS > 0` before the manager sets `maxFeeMintAmountPerDay`, and any staking rewards accrue (TVL increases), the next call to `updateRSETHPrice()` reverts.
- This ordering mismatch is easy to encounter during deployment or after an upgrade that introduces the fee-minting feature.

---

### Recommendation

Add a zero-check at the top of `_checkAndUpdateDailyFeeMintLimit()` to treat `maxFeeMintAmountPerDay == 0` as "no limit configured — allow any amount":

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
+   if (maxFeeMintAmountPerDay == 0) return; // no limit set; skip enforcement

    if (block.timestamp >= feePeriodStartTime + 1 days) {
        currentPeriodMintedFeeAmount = 0;
        feePeriodStartTime = getCurrentPeriodStartTime();
    }

    if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
        revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
    }

    currentPeriodMintedFeeAmount += feeAmount;
}
```

This aligns with the existing behavior of `remainingDailyFeeMintLimit()` and makes the zero state mean "unlimited" consistently across the contract.

---

### Proof of Concept

1. Deploy `LRTOracle` with `lrtConfigAddr`. `maxFeeMintAmountPerDay` is `0` (default).
2. Admin calls `LRTConfig.setProtocolFeeInBPS(500)` (5% fee).
3. Manager does **not** call `setMaxFeeMintAmountPerDay()`.
4. Staking rewards accrue: `totalETHInProtocol` grows above `previousTVL`.
5. Any caller invokes `updateRSETHPrice()`.
6. Inside `_updateRsETHPrice()`: `protocolFeeInETH > 0` → `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` is called.
7. Inside `_checkAndUpdateDailyFeeMintLimit`: `0 + rsethAmountToMintAsProtocolFee > 0` → `true` → `revert DailyFeeMintLimitExceeded(...)`.
8. `updateRSETHPrice()` reverts. The rsETH price is frozen. Protocol fee is never minted. The same revert occurs for `updateRSETHPriceAsManager()`. [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L170-172)
```text
    function remainingDailyFeeMintLimit() external view returns (uint256) {
        if (maxFeeMintAmountPerDay == 0) return 0;

```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
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
