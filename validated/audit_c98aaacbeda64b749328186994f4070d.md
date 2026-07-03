### Title
`maxFeeMintAmountPerDay` Is Not Initialized, Causing `updateRSETHPrice()` to Permanently Revert When Protocol Fee Is Active — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle` introduces a daily fee-minting cap controlled by `maxFeeMintAmountPerDay`. This variable is never set in `initialize()` or `reinitialize()`, so it defaults to `0`. The internal guard `_checkAndUpdateDailyFeeMintLimit()` unconditionally reverts when `feeAmount > maxFeeMintAmountPerDay`, meaning every call to `updateRSETHPrice()` that would mint a protocol fee reverts with `DailyFeeMintLimitExceeded`. The protocol fee feature is permanently broken until an admin separately calls `setMaxFeeMintAmountPerDay()`.

---

### Finding Description

`LRTOracle` declares three daily-fee-minting state variables:

```solidity
uint256 public currentPeriodMintedFeeAmount;
uint256 public feePeriodStartTime;
uint256 public maxFeeMintAmountPerDay;
```

`feePeriodStartTime` is set in `reinitialize()`:

```solidity
function reinitialize(uint256 _feePeriodStartTime) external reinitializer(2) onlyLRTManager {
    ...
    feePeriodStartTime = _feePeriodStartTime;
}
```

But `maxFeeMintAmountPerDay` is **never assigned** in either `initialize()` or `reinitialize()`. It remains `0` until an admin explicitly calls `setMaxFeeMintAmountPerDay()`.

The internal enforcement function is:

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
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

When `maxFeeMintAmountPerDay == 0` and `feeAmount > 0`, the condition `0 + feeAmount > 0` is always `true`, so the function always reverts.

`_checkAndUpdateDailyFeeMintLimit` is called unconditionally inside `_updateRsETHPrice()`:

```solidity
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);   // <-- reverts
    ...
} else {
    _checkAndUpdateDailyFeeMintLimit(0);   // safe when feeAmount == 0
}
```

Whenever `protocolFeeInBPS > 0` (set in `LRTConfig`) and TVL increases, `protocolFeeInETH > 0`, and `updateRSETHPrice()` reverts.

The inconsistency is confirmed by `remainingDailyFeeMintLimit()`, which explicitly guards against the zero case:

```solidity
function remainingDailyFeeMintLimit() external view returns (uint256) {
    if (maxFeeMintAmountPerDay == 0) return 0;   // view handles it; internal function does not
    ...
}
```

This asymmetry confirms the internal function was not updated to handle the uninitialized state.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield / Temporary freezing of funds.**

1. **Protocol fee is never minted.** The treasury never receives its rsETH fee share. This is a permanent loss of unclaimed yield for the protocol.
2. **`updateRSETHPrice()` is blocked.** Because the public `updateRSETHPrice()` and manager-only `updateRSETHPriceAsManager()` both call `_updateRsETHPrice()`, neither can succeed when TVL grows. The rsETH price becomes stale, which means depositors receive an incorrect (inflated) rsETH amount, diluting existing holders — a form of temporary fund mis-accounting.

---

### Likelihood Explanation

The normal operating condition of the protocol is `protocolFeeInBPS > 0` (set via `LRTConfig.setProtocolFeeBps()`) and TVL growing over time (rewards accruing). Both conditions are expected to hold in production. The `reinitialize()` function sets `feePeriodStartTime` but omits `maxFeeMintAmountPerDay`, making it easy to deploy the fee-minting feature in a broken state without any explicit error at initialization time.

---

### Recommendation

Initialize `maxFeeMintAmountPerDay` inside `reinitialize()`:

```solidity
function reinitialize(uint256 _feePeriodStartTime, uint256 _maxFeeMintAmountPerDay)
    external reinitializer(2) onlyLRTManager
{
    ...
    feePeriodStartTime = _feePeriodStartTime;
    maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
}
```

Alternatively, add a bypass in `_checkAndUpdateDailyFeeMintLimit` to treat `maxFeeMintAmountPerDay == 0` as "no limit enforced":

```solidity
if (maxFeeMintAmountPerDay == 0) return; // no cap configured yet
```

---

### Proof of Concept

1. `LRTOracle` is deployed and `initialize(lrtConfigAddr)` is called — `maxFeeMintAmountPerDay` is `0`.
2. `reinitialize(_feePeriodStartTime)` is called — `feePeriodStartTime` is set, but `maxFeeMintAmountPerDay` remains `0`.
3. `LRTConfig.setProtocolFeeBps(500)` is called (5% fee).
4. EigenLayer rewards accrue; TVL increases.
5. Anyone calls `updateRSETHPrice()`.
6. Inside `_updateRsETHPrice()`: `protocolFeeInETH > 0` → `rsethAmountToMintAsProtocolFee > 0` → `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` → `0 + rsethAmountToMintAsProtocolFee > 0` → **reverts with `DailyFeeMintLimitExceeded`**.
7. The rsETH price is never updated; the treasury never receives fees. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L32-35)
```text
    // Daily fee minting limit variables
    uint256 public currentPeriodMintedFeeAmount;
    uint256 public feePeriodStartTime;
    uint256 public maxFeeMintAmountPerDay;
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
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

**File:** contracts/LRTOracle.sol (L170-181)
```text
    function remainingDailyFeeMintLimit() external view returns (uint256) {
        if (maxFeeMintAmountPerDay == 0) return 0;

        // If we're on a new day but no mint has occurred yet, treat currentPeriodMintedAmount as 0
        uint256 effectiveDailyFeeMintAmount =
            (block.timestamp >= feePeriodStartTime + 1 days) ? 0 : currentPeriodMintedFeeAmount;

        return
            maxFeeMintAmountPerDay > effectiveDailyFeeMintAmount
                ? maxFeeMintAmountPerDay - effectiveDailyFeeMintAmount
                : 0;
    }
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

**File:** contracts/LRTOracle.sol (L298-311)
```text
        // mint protocol fee as rsETH if there's a fee to take
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
