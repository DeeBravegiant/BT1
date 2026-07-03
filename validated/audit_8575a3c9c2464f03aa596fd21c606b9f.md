### Title
Zero-initialized `rsETHPrice` Causes Permanent DoS on Public `updateRSETHPrice()` When `pricePercentageLimit > 0` — (`contracts/LRTOracle.sol`)

---

### Summary

When `rsETHPrice == 0` (never updated post-deployment) and `rsethSupply > 0`, the `highestRsethPrice == 0` guard at line 224 sets `highestRsethPrice = rsETHPrice = 0`. Every subsequent non-manager call to `updateRSETHPrice()` then computes `newRsETHPrice > 0` against a baseline of `0`, and the threshold check `priceDifference > pricePercentageLimit.mulWad(0)` reduces to `newRsETHPrice > 0`, which is always true. Non-managers always revert with `PriceAboveDailyThreshold`.

---

### Finding Description

**Root cause — lines 224–226 and 252–257:**

```solidity
// Line 224-226
if (highestRsethPrice == 0) {
    highestRsethPrice = rsETHPrice;   // rsETHPrice is 0 → highestRsethPrice stays 0
}
``` [1](#0-0) 

```solidity
// Line 252-257
if (newRsETHPrice > highestRsethPrice) {          // Y > 0 → true
    uint256 priceDifference = newRsETHPrice - highestRsethPrice;  // = Y
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 &&
        priceDifference > pricePercentageLimit.mulWad(highestRsethPrice); // Y > mulWad(0)=0 → always true
``` [2](#0-1) 

**Execution trace for the vulnerable state:**

| Variable | Value |
|---|---|
| `rsETHPrice` | `0` (never set) |
| `highestRsethPrice` | `0` (never set) |
| `rsethSupply` | `> 0` (users deposited) |
| `pricePercentageLimit` | `> 0` (e.g. `1e16`) |

1. `rsethSupply > 0` → skip the early-return branch at line 218. [3](#0-2) 
2. `highestRsethPrice == 0` → `highestRsethPrice = rsETHPrice = 0`. [1](#0-0) 
3. `previousTVL = rsethSupply.mulWad(0) = 0`. [4](#0-3) 
4. `totalETHInProtocol > 0` → fee computed, `newRsETHPrice > 0`. [5](#0-4) 
5. `newRsETHPrice > highestRsethPrice (0)` → enter threshold block.
6. `priceDifference = newRsETHPrice`; `pricePercentageLimit.mulWad(0) = 0`; `priceDifference > 0` → `isPriceIncreaseOffLimit = true`. [6](#0-5) 
7. Non-manager → **revert `PriceAboveDailyThreshold()`**. [7](#0-6) 

The public entry point `updateRSETHPrice()` is permanently inaccessible to unprivileged callers in this state. [8](#0-7) 

---

### Impact Explanation

**Correct impact: Medium. Temporary freezing of funds.**

The claim of *Critical / Permanent* is overstated. The manager can always call `updateRSETHPriceAsManager()`, which bypasses the revert at line 263 and bootstraps `rsETHPrice` and `highestRsethPrice` to correct non-zero values in a single transaction. [9](#0-8) 

After that one manager call, `highestRsethPrice > 0` and subsequent non-manager calls succeed normally. The freeze is therefore **temporary and manager-recoverable**, not permanent.

The real impact is:
- Until the manager bootstraps the price, `rsETHPrice` remains `0`.
- Any protocol component reading `rsETHPrice` (deposits, withdrawals, share math) operates on a zero price, which can cause incorrect accounting.
- Public keepers/bots cannot perform their expected role of keeping the price fresh.

---

### Likelihood Explanation

**Low-to-Medium.** The state arises naturally if:
1. The protocol is deployed and users deposit (minting rsETH, so `rsethSupply > 0`).
2. Admin calls `setPricePercentageLimit` with a non-zero value.
3. No one calls `updateRSETHPriceAsManager()` first to bootstrap the price.

No attacker action is required; this is a pure initialization-ordering bug. The window closes as soon as the manager bootstraps the price.

---

### Recommendation

In the `highestRsethPrice == 0` guard, do not blindly copy `rsETHPrice` when it is also `0`. Instead, seed `highestRsethPrice` with the freshly computed `newRsETHPrice` (computed later in the same function), or add an explicit check:

```solidity
if (highestRsethPrice == 0) {
    // Only seed from rsETHPrice if it is non-zero; otherwise defer to newRsETHPrice below
    if (rsETHPrice > 0) {
        highestRsethPrice = rsETHPrice;
    }
}
```

And after computing `newRsETHPrice`, add a fallback:

```solidity
if (highestRsethPrice == 0) {
    highestRsethPrice = newRsETHPrice; // bootstrap from first real price
}
```

This ensures the threshold comparison is always against a meaningful baseline.

---

### Proof of Concept

```solidity
// Pseudocode unit test (local fork or mock environment)
// State: rsETHPrice = 0, highestRsethPrice = 0, pricePercentageLimit = 1e16
// rsethSupply > 0 (users have deposited)

vm.prank(nonManager);
vm.expectRevert(ILRTOracle.PriceAboveDailyThreshold.selector);
lrtOracle.updateRSETHPrice();

// Manager can still fix it:
vm.prank(manager);
lrtOracle.updateRSETHPriceAsManager(); // succeeds, sets rsETHPrice > 0

// Now non-manager can call again:
vm.prank(nonManager);
lrtOracle.updateRSETHPrice(); // succeeds
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

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L224-226)
```text
        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L234-234)
```text
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-257)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L263-265)
```text
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```
