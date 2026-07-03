### Title
Unrestricted `updateRSETHPrice()` Allows Any Caller to Exhaust the Daily Fee Minting Limit, Permanently Blocking Protocol Fee Collection for the Remainder of the Period - (File: contracts/LRTOracle.sol)

---

### Summary
`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard. Any unprivileged caller can invoke it at a strategically chosen moment to consume the entire `maxFeeMintAmountPerDay` budget in a single transaction. Any subsequent legitimate call within the same 24-hour window that would mint additional fees reverts with `DailyFeeMintLimitExceeded`, permanently destroying the protocol's fee revenue from rewards that accrue after the limit is exhausted.

---

### Finding Description

`LRTOracle.sol` exposes two entry points for updating the rsETH price:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}

// contracts/LRTOracle.sol:94-96
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
``` [1](#0-0) 

The manager-gated variant exists precisely because the developers recognised that price updates require privilege in certain conditions (e.g., when the price exceeds the daily threshold). However, the public variant carries no such restriction.

Every call to `_updateRsETHPrice()` unconditionally invokes `_checkAndUpdateDailyFeeMintLimit()`:

```solidity
// contracts/LRTOracle.sol:299-311
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
    ...
} else {
    _checkAndUpdateDailyFeeMintLimit(0);
}
``` [2](#0-1) 

`_checkAndUpdateDailyFeeMintLimit` accumulates into `currentPeriodMintedFeeAmount` and hard-reverts if the cap is breached:

```solidity
// contracts/LRTOracle.sol:197-210
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
    if (block.timestamp >= feePeriodStartTime + 1 days) {
        currentPeriodMintedFeeAmount = 0;
        feePeriodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
        revert DailyFeeMintLimitExceeded(...);
    }
    currentPeriodMintedFeeAmount += feeAmount;
}
``` [3](#0-2) 

Because `updateRSETHPrice()` is public, an attacker can call it the moment a large reward batch lands (e.g., EigenLayer restaking rewards, stETH rebases), minting the corresponding fee rsETH and pushing `currentPeriodMintedFeeAmount` to or near `maxFeeMintAmountPerDay`. Any further rewards that accrue within the same 24-hour window cannot be fee-collected: every subsequent call to `updateRSETHPrice()` reverts, and those fees are permanently lost.

The `currentPeriodMintedFeeAmount` variable is the direct analog of `collectedFee` in the reference report: it is a global accounting variable that any unprivileged caller can advance to its ceiling, corrupting the protocol's fee-collection accounting for the rest of the period.

---

### Impact Explanation

**Permanent freezing of unclaimed yield (Medium).**

Protocol fee revenue from rewards that accrue after the daily limit is exhausted is irrecoverably lost for that day. The `_checkAndUpdateDailyFeeMintLimit` revert prevents any further fee minting until the next period resets `currentPeriodMintedFeeAmount`. The treasury receives fewer rsETH fees than it is entitled to, constituting permanent loss of unclaimed yield. [4](#0-3) 

---

### Likelihood Explanation

- No capital, no role, no special setup required — a single EOA call suffices.
- The attacker only needs to monitor on-chain TVL (public data) and call `updateRSETHPrice()` immediately after a significant reward event.
- The attack is repeatable every day.
- Gas cost is the only barrier, which is negligible relative to the fee revenue destroyed.

---

### Recommendation

Restrict `updateRSETHPrice()` to authorised callers, consistent with the already-existing `updateRSETHPriceAsManager()`:

```solidity
// Before (vulnerable)
function updateRSETHPrice() public whenNotPaused { ... }

// After (fixed)
function updateRSETHPrice() external whenNotPaused onlyLRTManager { ... }
```

Alternatively, introduce a dedicated `ORACLE_UPDATER_ROLE` granted to the protocol's automation keeper, so that off-chain bots can still trigger updates without requiring full manager privileges.

---

### Proof of Concept

1. `maxFeeMintAmountPerDay` = 100 rsETH; a new 24-hour period begins.
2. EigenLayer distributes rewards; TVL increases by an amount that corresponds to 95 rsETH in protocol fees.
3. Attacker calls `LRTOracle.updateRSETHPrice()` → `_checkAndUpdateDailyFeeMintLimit(95)` succeeds; `currentPeriodMintedFeeAmount = 95`.
4. Six hours later, stETH rebases; TVL increases again, corresponding to 10 rsETH in additional fees.
5. Protocol keeper (or anyone) calls `updateRSETHPrice()` → `_checkAndUpdateDailyFeeMintLimit(10)` reverts: `DailyFeeMintLimitExceeded(105, 100)`.
6. The 10 rsETH in fees from step 4 are permanently unclaimable for this period. The treasury is short 10 rsETH of yield it was entitled to. [5](#0-4) [6](#0-5)

### Citations

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
