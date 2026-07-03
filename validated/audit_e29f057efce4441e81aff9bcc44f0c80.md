Audit Report

## Title
`_checkAndUpdateDailyFeeMintLimit` lacks zero-guard, causing `updateRSETHPrice()` to revert when `maxFeeMintAmountPerDay` is uninitialized and fees are owed — (`contracts/LRTOracle.sol`)

## Summary
When `maxFeeMintAmountPerDay` is zero (its default uninitialized value, never set in `initialize` or `reinitialize`) and the protocol accrues staking rewards (`totalETHInProtocol > previousTVL`) with a non-zero `protocolFeeInBPS`, every call to `updateRSETHPrice()` reverts with `DailyFeeMintLimitExceeded`. The price oracle is left stale until the manager calls `setMaxFeeMintAmountPerDay` with a non-zero value. The impact is correctly scoped to **Low — contract fails to deliver promised returns, but doesn't lose value**: deposits and withdrawals remain functional but operate on a stale `rsETHPrice`.

## Finding Description
`_checkAndUpdateDailyFeeMintLimit` enforces:

```solidity
// LRTOracle.sol line 205
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
```

When `maxFeeMintAmountPerDay == 0` and `feeAmount > 0`, this reduces to `feeAmount > 0 == true` — an unconditional revert. The sibling view function `remainingDailyFeeMintLimit()` correctly short-circuits:

```solidity
// LRTOracle.sol line 171
if (maxFeeMintAmountPerDay == 0) return 0;
```

but `_checkAndUpdateDailyFeeMintLimit` has no equivalent guard.

The fee-minting branch is entered whenever `protocolFeeInETH > 0` (lines 299–303), which occurs whenever the protocol is unpaused and TVL has grown (lines 244–247). Neither `initialize` (lines 64–68) nor `reinitialize` (lines 72–79) sets `maxFeeMintAmountPerDay`, so it defaults to zero on any fresh deployment or upgrade. Both public entry points — `updateRSETHPrice()` (line 87) and `updateRSETHPriceAsManager()` (line 94) — call the same `_updateRsETHPrice()`, so neither can succeed under these conditions. The price write at line 313 (`rsETHPrice = newRsETHPrice`) is never reached, leaving `rsETHPrice` stale.

Note: the `else` branch at line 310 calls `_checkAndUpdateDailyFeeMintLimit(0)`, which evaluates `0 > 0 == false` and does not revert — so the bug only manifests when fees are actually owed.

## Impact Explanation
`rsETHPrice` is consumed by `LRTDepositPool.getRsETHAmountToMint()` (line 520) and `LRTWithdrawalManager.getExpectedAssetAmount()` (line 593) for deposit and withdrawal pricing. A stale price causes incorrect rsETH minting ratios and incorrect withdrawal valuations, but does not prevent users from depositing or withdrawing. This matches **Low — contract fails to deliver promised returns, but doesn't lose value**. The submitted claim of "Medium — temporary freezing of funds" is not supported: no code path prevents users from accessing their funds; they continue to operate at the stale price.

## Likelihood Explanation
`maxFeeMintAmountPerDay` is zero by default and is not initialized in either `initialize` or `reinitialize`. Any deployment or upgrade where the manager omits a call to `setMaxFeeMintAmountPerDay` before staking rewards accrue will trigger this. The manager may also explicitly reset it to zero intending to "disable" fee minting, unaware that it blocks all price updates. No attacker is required — normal protocol operation (TVL growth from staking rewards) is sufficient.

## Recommendation
Add a zero-bypass guard in `_checkAndUpdateDailyFeeMintLimit`, consistent with the existing logic in `remainingDailyFeeMintLimit`:

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
    if (maxFeeMintAmountPerDay == 0) return; // treat 0 as "no limit / fee minting disabled"
    // ... existing logic
}
```

Alternatively, skip the `_checkAndUpdateDailyFeeMintLimit` call in `_updateRsETHPrice` entirely when `maxFeeMintAmountPerDay == 0`.

## Proof of Concept

```solidity
// Preconditions:
//   maxFeeMintAmountPerDay = 0 (default, never set after deploy/upgrade)
//   lrtConfig.protocolFeeInBPS() > 0
//   rsethSupply > 0
//   totalETHInProtocol > rsethSupply * rsETHPrice (TVL grew via staking rewards)
//   protocol not paused

function test_updateRSETHPrice_revertsWhenMaxFeeMintIsZeroAndTVLGrew() public {
    assertEq(lrtOracle.maxFeeMintAmountPerDay(), 0);

    // Mock asset oracle to return a higher price, simulating TVL growth
    // ...setup mocks so totalETHInProtocol > previousTVL...

    // Any caller — including an unprivileged user — triggers the revert
    vm.expectRevert(
        abi.encodeWithSelector(LRTOracle.DailyFeeMintLimitExceeded.selector, feeAmount, 0)
    );
    lrtOracle.updateRSETHPrice();

    // rsETHPrice is never written — stale
    assertEq(lrtOracle.rsETHPrice(), previousPrice);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L64-79)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }

    /// @notice Initializes the contract with a fee period start time
    /// @param _feePeriodStartTime The start time of the fee period
    function reinitialize(uint256 _feePeriodStartTime) external reinitializer(2) onlyLRTManager {
        if (_feePeriodStartTime > block.timestamp || _feePeriodStartTime <= block.timestamp - 1 days) {
            revert PeriodStartTimeShouldBeWithin24Hours();
        }

        feePeriodStartTime = _feePeriodStartTime;
        emit FeePeriodStartTimeSet(_feePeriodStartTime);
    }
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L170-171)
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

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-313)
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

        rsETHPrice = newRsETHPrice;
```
