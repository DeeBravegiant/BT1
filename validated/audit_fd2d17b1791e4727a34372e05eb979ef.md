### Title
Unprivileged Caller Can Trigger Protocol-Wide Pause via Transient Oracle Price Dip — (`contracts/LRTOracle.sol`)

---

### Summary

`updateRSETHPrice()` is a permissionless `public` function. Its internal logic auto-pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself whenever the computed `newRsETHPrice` falls below `highestRsethPrice` by more than `pricePercentageLimit`. Any unprivileged address can call this function at a moment of transient oracle price weakness, permanently freezing deposits and withdrawals until an admin manually unpauses.

---

### Finding Description

`updateRSETHPrice()` carries no role guard: [1](#0-0) 

Inside `_updateRsETHPrice()`, the downside-protection branch is: [2](#0-1) 

The condition at line 274 compares `diff > pricePercentageLimit.mulWad(highestRsethPrice)` with no caller-identity check. When it fires, the oracle calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` on itself, then returns early — leaving the entire protocol frozen.

`highestRsethPrice` is a monotonically non-decreasing watermark (updated only when `newRsETHPrice > highestRsethPrice`): [3](#0-2) 

This means even a brief, legitimate market dip in any supported asset's oracle price — one that recovers within minutes — is enough to satisfy the condition. The attacker's only job is to call `updateRSETHPrice()` at that moment.

The `_pause()` helper on `LRTOracle` is idempotent but irreversible without admin action: [4](#0-3) 

Unpausing requires `onlyLRTAdmin`: [5](#0-4) 

---

### Impact Explanation

All three pause calls execute atomically in a single transaction triggered by `address(1)`. After the call:
- `LRTDepositPool.paused() == true` → no deposits
- `LRTWithdrawalManager.paused() == true` → no withdrawals/claims
- `LRTOracle.paused == true` → `updateRSETHPrice()` itself is blocked (`whenNotPaused`)

User funds are frozen until an admin manually calls `unpause()` on each contract. This is **Temporary freezing of funds** (Medium).

---

### Likelihood Explanation

- `pricePercentageLimit` is set by admin (e.g., 1% = `1e16`). A 1% intraday dip in stETH or ETHx is routine.
- `highestRsethPrice` is the all-time peak, so even a recovery from a prior dip leaves the protocol permanently vulnerable to the next dip below that peak.
- No MEV, no front-running, no oracle manipulation required — just a public call at the right block.
- The attacker pays only gas.

---

### Recommendation

Restrict the auto-pause trigger to privileged callers only. Two options:

1. **Role-gate the pause branch**: inside `_updateRsETHPrice()`, only execute the `lrtDepositPool.pause()` / `withdrawalManager.pause()` / `_pause()` calls when `msg.sender` holds `PAUSER_ROLE` or `MANAGER`. For unprivileged callers, revert instead (mirroring the upside-threshold behavior at lines 263–265).

2. **Separate concerns**: remove the auto-pause side-effect from `_updateRsETHPrice()` entirely. Emit an event on price-drop-beyond-threshold and let an off-chain keeper with `PAUSER_ROLE` react. [6](#0-5) 

The upside branch already does this correctly — it reverts for non-managers. The downside branch should mirror that pattern.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (local Anvil fork, no public mainnet)
// Assumes: pricePercentageLimit = 1e16 (1%)
//          highestRsethPrice already set from a prior updateRSETHPrice() call

contract MockPriceFetcher {
    uint256 public price;
    constructor(uint256 _price) { price = _price; }
    function getAssetPrice(address) external view returns (uint256) { return price; }
    function setPrice(uint256 _price) external { price = _price; }
}

// In the test:
// 1. Deploy protocol, set pricePercentageLimit = 1e16
// 2. Call updateRSETHPrice() once at normal price → highestRsethPrice is set
// 3. Drop mock oracle price by 2% (below the 1% limit)
// 4. Call updateRSETHPrice() as address(1) (unprivileged)
// 5. Assert:
//    assertEq(lrtDepositPool.paused(), true);
//    assertEq(withdrawalManager.paused(), true);
//    assertEq(lrtOracle.paused(), true);
```

The call at step 4 succeeds with no revert because `updateRSETHPrice()` has no role check, and the price-drop branch at lines 277–281 executes unconditionally for any caller. [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L143-146)
```text
    function unpause() external whenPaused onlyLRTAdmin {
        paused = false;
        emit Unpaused(msg.sender);
    }
```

**File:** contracts/LRTOracle.sol (L260-266)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L294-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L319-323)
```text
    function _pause() internal {
        if (paused) return;
        paused = true;
        emit Paused(msg.sender);
    }
```
