### Title
Dual Independent Daily-Mint Limits Allow RSETH Token-Level Cap to Permanently Block Oracle Fee Minting — (`contracts/LRTOracle.sol` / `contracts/RSETH.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` mints protocol fees through `IRSETH.mint()`, which enforces `RSETH.checkDailyMintLimit`. A separate, independent cap (`LRTOracle.maxFeeMintAmountPerDay`) guards the oracle side. Because the two limits are set independently and the RSETH token-level cap applies to **all** mints (user deposits + fee mints combined), a configuration where user minting has already consumed most of `RSETH.maxMintAmountPerDay` causes the fee mint to revert. Since the entire `_updateRsETHPrice()` call reverts, `rsETHPrice` is never updated, the fee accumulates across periods, and once the accumulated fee exceeds `LRTOracle.maxFeeMintAmountPerDay`, the oracle-level check also reverts — permanently blocking both fee minting and price updates.

---

### Finding Description

**Call path:**

`updateRSETHPrice()` (public, no access control) [1](#0-0) 
→ `_updateRsETHPrice()` computes `protocolFeeInETH` and `rsethAmountToMintAsProtocolFee` [2](#0-1) 
→ `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` passes (oracle-level cap not yet exceeded) [3](#0-2) 
→ `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)` [4](#0-3) 
→ `RSETH.mint` applies `checkDailyMintLimit(amount)` modifier [5](#0-4) 
→ `currentPeriodMintedAmount + amount > maxMintAmountPerDay` → `revert DailyMintLimitExceeded` [6](#0-5) 

Because the revert unwinds the entire transaction, `rsETHPrice` is never written and `currentPeriodMintedFeeAmount` is rolled back. On the next call, `previousTVL = rsethSupply × rsETHPrice` still uses the stale price, so `rewardAmount` (and therefore `rsethAmountToMintAsProtocolFee`) grows with each blocked period. Once the accumulated fee amount exceeds `maxFeeMintAmountPerDay`, `_checkAndUpdateDailyFeeMintLimit` itself reverts with `DailyFeeMintLimitExceeded`, and no further price update is possible at all. [7](#0-6) 

The two limits are set by independent manager calls with no cross-validation:

- `RSETH.setMaxMintAmountPerDay` [8](#0-7) 
- `LRTOracle.setMaxFeeMintAmountPerDay` [9](#0-8) 

Neither setter checks that `RSETH.maxMintAmountPerDay` leaves sufficient headroom for fee minting on top of expected user minting. [8](#0-7) 

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Protocol fee yield (rsETH minted to treasury) is permanently lost for any period in which the RSETH token-level daily cap is exhausted by user mints before the fee mint executes. As the fee accumulates across blocked periods, it eventually exceeds `maxFeeMintAmountPerDay`, after which `updateRSETHPrice()` is permanently bricked — no price update, no fee minting, no carry-forward. [10](#0-9) 

---

### Likelihood Explanation

Moderate. The two limits are set independently by the LRT Manager with no enforcement of their relationship. A high-volume deposit day (users consuming most of `maxMintAmountPerDay`) combined with a non-trivial protocol fee rate is sufficient. No attacker action is required — the condition arises from ordinary protocol usage under a plausible configuration. [11](#0-10) 

---

### Recommendation

1. **Reserve headroom**: When setting `RSETH.maxMintAmountPerDay`, account for the maximum daily fee mint (`maxFeeMintAmountPerDay`) so that `maxMintAmountPerDay ≥ expected_user_minting + maxFeeMintAmountPerDay`.
2. **Separate fee-mint path**: Grant the oracle a dedicated mint role that bypasses `checkDailyMintLimit`, or add a separate `mintFee` function in `RSETH` with its own cap, so fee minting is never blocked by user-deposit volume.
3. **Cross-validate on setter**: In `setMaxMintAmountPerDay` and `setMaxFeeMintAmountPerDay`, assert that `maxMintAmountPerDay >= maxFeeMintAmountPerDay`.

---

### Proof of Concept

```solidity
// Preconditions:
// RSETH.maxMintAmountPerDay = 1000e18
// LRTOracle.maxFeeMintAmountPerDay = 100e18
// Users have already minted 990e18 rsETH in the current period
// (currentPeriodMintedAmount = 990e18)
// Protocol fee for the period = 20e18 rsETH

// Step 1: any EOA calls updateRSETHPrice()
// Step 2: protocolFeeInETH > 0, rsethAmountToMintAsProtocolFee = 20e18
// Step 3: _checkAndUpdateDailyFeeMintLimit(20e18) passes (0 + 20e18 <= 100e18)
// Step 4: IRSETH.mint(treasury, 20e18) called
// Step 5: checkDailyMintLimit: 990e18 + 20e18 = 1010e18 > 1000e18 → DailyMintLimitExceeded revert
// Step 6: entire tx reverts; rsETHPrice unchanged; fee not minted

// After N blocked periods, rsethAmountToMintAsProtocolFee = N * 20e18
// When N = 6: 120e18 > maxFeeMintAmountPerDay (100e18)
// → _checkAndUpdateDailyFeeMintLimit reverts with DailyFeeMintLimitExceeded
// → updateRSETHPrice() is permanently bricked; fee yield permanently frozen
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L132-135)
```text
    function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
        maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
        emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
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

**File:** contracts/RSETH.sol (L125-128)
```text
    function setMaxMintAmountPerDay(uint256 _maxMintAmountPerDay) external onlyLRTManager {
        maxMintAmountPerDay = _maxMintAmountPerDay;
        emit MaxMintAmountPerDayUpdated(_maxMintAmountPerDay);
    }
```

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
```
