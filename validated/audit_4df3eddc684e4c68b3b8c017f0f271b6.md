All cited code references check out against the actual source. Let me verify the key facts:

- `updateRSETHPrice()` is `public whenNotPaused` with no role check. [1](#0-0) 
- `SwETHPriceOracle.getAssetPrice()` returns raw `ISwETH(swETHAddress).getRate()` with no smoothing. [2](#0-1) 
- The circuit-breaker at lines 270–282 pauses `lrtDepositPool`, `withdrawalManager`, and `LRTOracle` itself when `newRsETHPrice` drops more than `pricePercentageLimit` below `highestRsethPrice`. [3](#0-2) 
- `LRTOracle.unpause()` is `onlyLRTAdmin`. [4](#0-3) 
- `LRTDepositPool.unpause()` is `onlyLRTAdmin`. [5](#0-4) 
- `LRTDepositPool.pause()` requires `PAUSER_ROLE`, which `LRTOracle` must hold for the circuit-breaker to function as designed. [6](#0-5) 

All preconditions are realistic, the exploit path is concrete, and the impact (temporary freezing of deposits and withdrawals) matches an explicitly allowed scope item.

---

Audit Report

## Title
Unprivileged Caller Can Trigger Circuit-Breaker Pause via Spot Oracle Dip — (`contracts/LRTOracle.sol`)

## Summary
`LRTOracle.updateRSETHPrice()` is a public function callable by any EOA or contract. Its internal circuit-breaker compares a freshly computed spot price against the stored all-time-high `highestRsethPrice` and, if the drop exceeds `pricePercentageLimit`, immediately pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`. Because `SwETHPriceOracle` forwards the raw `ISwETH.getRate()` with no time-weighted averaging, any block in which the computed rsETH price is transiently below the threshold is sufficient for an unprivileged actor to freeze all deposits and withdrawals until an admin manually unpauses.

## Finding Description
**No access control on `updateRSETHPrice()`:**
`LRTOracle.updateRSETHPrice()` carries only the `whenNotPaused` modifier; any address may call it at any time the contract is unpaused.

**Raw spot price in `SwETHPriceOracle`:**
`SwETHPriceOracle.getAssetPrice()` returns `ISwETH(swETHAddress).getRate()` directly. This value is consumed without smoothing inside `_getTotalEthInProtocol()` → `getAssetPrice()` → `_updateRsETHPrice()`.

**Circuit-breaker fires on any spot dip past the threshold:**
Inside `_updateRsETHPrice()` (lines 270–282), if `newRsETHPrice < highestRsethPrice` and `diff > pricePercentageLimit.mulWad(highestRsethPrice)`, the function calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` on itself, then returns. `highestRsethPrice` is the all-time-high, so even a brief, transient dip in any supported asset's oracle rate can satisfy the condition.

**No auto-unpause:**
Both `LRTOracle.unpause()` and `LRTDepositPool.unpause()` are restricted to `onlyLRTAdmin`. The freeze persists until an admin responds.

**Why existing checks fail:**
The `whenNotPaused` guard only prevents re-entrancy after the pause is already set; it provides no protection against the initial unprivileged call that triggers the pause. The `pricePercentageLimit` guard is the intended safety mechanism, but it is reachable by anyone.

## Impact Explanation
All user-facing deposit (`depositETH`, `depositAsset`) and withdrawal operations are gated by `whenNotPaused` on `LRTDepositPool` and `LRTWithdrawalManager`. A successful trigger freezes every deposit and withdrawal until admin intervention. This is a concrete **Medium — Temporary freezing of funds** impact.

## Likelihood Explanation
Required conditions:
1. `pricePercentageLimit > 0` — admin-configured and intended to be set in production.
2. `LRTOracle` holds `PAUSER_ROLE` on `LRTDepositPool` and `LRTWithdrawalManager` — required for the circuit-breaker feature to function at all, so this is the expected deployment state.
3. A block exists where the computed rsETH price is below `highestRsethPrice * (1 - pricePercentageLimit)` — achievable during a swETH rebase window, a slashing event, or any transient dip in any supported LST oracle rate.

No privileged access, no oracle compromise, and no front-running is required. The attacker monitors the swETH (or other LST) rate off-chain and submits `updateRSETHPrice()` in the same block where the dip is visible. The attack is repeatable: after admin unpauses, the attacker can trigger it again in the next qualifying block.

## Recommendation
1. **Restrict `updateRSETHPrice()` to a privileged role** (e.g., `onlyLRTManager` or a dedicated keeper role), or enforce a minimum call interval (e.g., once per epoch), so unprivileged callers cannot reach the circuit-breaker.
2. **Use a TWAP or rate-smoothing layer** in `SwETHPriceOracle` instead of forwarding the raw instantaneous `getRate()`, so transient dips do not satisfy the circuit-breaker condition.
3. **Decouple the pause trigger from the price-update path**: introduce a separate, role-gated `triggerCircuitBreaker()` function so that an unprivileged price update can compute and store the new price without being able to directly pause the protocol.

## Proof of Concept
```solidity
// Fork test at a block where the computed rsETH price is transiently below
// highestRsethPrice * (1 - pricePercentageLimit).
// Preconditions: pricePercentageLimit > 0, LRTOracle holds PAUSER_ROLE.

function testGriefPause() external {
    // 1. Confirm pre-state: protocol is live
    assertFalse(lrtDepositPool.paused());
    assertFalse(withdrawalManager.paused());
    assertFalse(lrtOracle.paused());

    // 2. Unprivileged caller invokes updateRSETHPrice at the dip block
    vm.prank(address(0xdead));
    lrtOracle.updateRSETHPrice(); // no role check — succeeds

    // 3. Circuit-breaker fired: all three contracts are now paused
    assertTrue(lrtDepositPool.paused());
    assertTrue(withdrawalManager.paused());
    assertTrue(lrtOracle.paused());

    // 4. Advance one block — rate recovers, but protocol remains paused (no auto-unpause)
    vm.roll(block.number + 1);
    assertTrue(lrtDepositPool.paused());

    // 5. Normal user deposit reverts
    vm.expectRevert("Pausable: paused");
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
}
```

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

**File:** contracts/oracles/SwETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != swETHAddress) {
            revert InvalidAsset();
        }

        return ISwETH(swETHAddress).getRate();
    }
```

**File:** contracts/LRTDepositPool.sol (L349-351)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }
```

**File:** contracts/LRTDepositPool.sol (L354-356)
```text
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```
