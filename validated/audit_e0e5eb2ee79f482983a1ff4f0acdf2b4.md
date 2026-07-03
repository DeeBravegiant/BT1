### Title
Shared `checkDailyMintLimit` Cap in RSETH.sol Allows User Deposits to Permanently Block Protocol Fee Minting - (File: contracts/RSETH.sol, contracts/LRTOracle.sol)

### Summary

The `checkDailyMintLimit` modifier in `RSETH.sol` applies to **all** callers of `IRSETH.mint`, including both user deposits (via `LRTDepositPool`) and protocol fee mints (via `LRTOracle._updateRsETHPrice`). When user deposits exhaust `maxMintAmountPerDay` within a period, the subsequent fee mint inside `_updateRsETHPrice` reverts with `DailyMintLimitExceeded`, causing the entire price-update transaction to revert and permanently losing that period's protocol fee yield. The uninitialized `periodStartTime = 0` state (before `reinitialize` is called) is a contributing precondition but is not strictly required — the same failure path exists with any initialized period start.

### Finding Description

**Root cause — shared cap, no fee-mint exemption:**

`RSETH.sol` `checkDailyMintLimit` (lines 42–56) guards every call to `mint()`:

```solidity
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
    }
    currentPeriodMintedAmount += amount;
    _;
}
``` [1](#0-0) 

`LRTDepositPool._mintRsETH` calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)`, consuming from this same cap. [2](#0-1) 

`LRTOracle._updateRsETHPrice` later calls `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)` — also gated by the same `checkDailyMintLimit`: [3](#0-2) 

There is no exemption or separate allowance for fee mints.

**Uninitialized-state amplifier:**

Before `reinitialize` is called, `periodStartTime = 0`. The condition `block.timestamp >= 0 + 1 days` is always true, so the very first `mint()` call resets the period to `getCurrentPeriodStartTime()` (Unix-epoch-aligned midnight) and sets `currentPeriodMintedAmount = 0`. This means the full cap is available from the first deposit, and a single large deposit can fill it entirely within the same period that `updateRSETHPrice()` will attempt to mint fees. [4](#0-3) [5](#0-4) 

**Revert propagation — fee is permanently lost:**

`_updateRsETHPrice` calls `_checkAndUpdateDailyFeeMintLimit` (oracle-side limit, passes) then `IRSETH.mint` (RSETH-side limit, reverts). Because the revert unwinds the entire call, `rsETHPrice` is never updated and the fee is never minted. The next invocation of `updateRSETHPrice()` computes the fee delta from the **stale** previous price, so the yield accrued during the blocked period is permanently unaccounted for and lost. [6](#0-5) 

### Impact Explanation

Protocol fee yield accrued during any period in which user deposits exhaust `maxMintAmountPerDay` is permanently frozen. `updateRSETHPrice()` cannot complete, the price is not updated, and the fee rsETH is never minted to the treasury. No recovery mechanism exists.

### Likelihood Explanation

- Requires `maxMintAmountPerDay` to be set to a finite, reachable value (zero disables all minting including deposits, so a non-zero cap must be configured).
- A single depositor or the aggregate of depositors within one period must reach the cap before `updateRSETHPrice()` is called.
- The uninitialized state (`periodStartTime = 0`) makes the window deterministic (midnight UTC boundary) and ensures the cap is fully available from the first deposit, increasing exploitability during the upgrade window before `reinitialize` is called.
- `updateRSETHPrice()` is a public, permissionless function, so an attacker can also time the call to maximize damage.

### Recommendation

1. **Exempt fee mints from the RSETH.sol cap**, or maintain a separate, dedicated daily allowance for protocol fee mints that is not shared with user deposit mints.
2. **Alternatively**, in `_updateRsETHPrice`, catch the revert from `IRSETH.mint` (or pre-check remaining capacity) and skip fee minting gracefully rather than reverting the entire price update.
3. **Enforce atomicity of upgrade + `reinitialize`** so `periodStartTime` is never left at zero while `maxMintAmountPerDay` is non-zero.

### Proof of Concept

```solidity
// 1. Deploy RSETH + LRTDepositPool + LRTOracle without calling reinitialize
//    => periodStartTime == 0, maxMintAmountPerDay == CAP (set by manager)

// 2. User deposits ETH equal to CAP worth of rsETH
lrtDepositPool.depositETH{value: capEquivalentETH}(0, "");
// => checkDailyMintLimit resets period (periodStartTime = 0 + 1 days condition true),
//    currentPeriodMintedAmount = CAP

// 3. Yield accrues (e.g., staking rewards increase totalETHInProtocol)

// 4. Anyone calls updateRSETHPrice()
lrtOracle.updateRSETHPrice();
// => _checkAndUpdateDailyFeeMintLimit(fee) passes (oracle-side limit not exhausted)
// => IRSETH.mint(treasury, fee) hits checkDailyMintLimit:
//    currentPeriodMintedAmount(CAP) + fee > maxMintAmountPerDay(CAP) => REVERT DailyMintLimitExceeded
// => entire updateRSETHPrice() reverts; fee yield permanently lost

// 5. Assert: rsETHPrice unchanged, treasury balance unchanged
```

### Citations

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/RSETH.sol (L257-261)
```text
    function getCurrentPeriodStartTime() public view returns (uint256) {
        // Calculate the full (complete) days elapsed since the period start time (floors the result)
        uint256 daysElapsed = (block.timestamp - periodStartTime) / 1 days;
        return periodStartTime + daysElapsed * 1 days;
    }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
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
