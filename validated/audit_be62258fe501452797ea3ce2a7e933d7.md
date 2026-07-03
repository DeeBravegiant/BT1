The code at [1](#0-0)  confirms the asymmetry exactly as described. The ETH branch at line 679 omits `+ amount`, while the ERC20 branch at line 681 correctly includes it. The call chain through `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` is confirmed at [2](#0-1)  and [3](#0-2) .

---

Audit Report

## Title
ETH Deposit Limit Check Omits Incoming Amount, Allowing Cap Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit()` applies an asymmetric comparison: for ERC20 assets it correctly evaluates `totalAssetDeposits + amount > depositLimit`, but for ETH it evaluates only `totalAssetDeposits > depositLimit`, omitting the incoming `amount`. As a result, any ETH deposit made when `getTotalAssetDeposits(ETH_TOKEN) == depositLimitByAsset[ETH_TOKEN]` passes the gate and mints rsETH, silently pushing total ETH deposits above the configured cap.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← missing `+ amount`
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH branch only returns `true` (triggering revert) when the limit is **already exceeded** before the deposit. When `totalAssetDeposits == depositLimit`, the expression evaluates to `false`, so `_beforeDeposit` does not revert and the deposit proceeds. The ERC20 branch correctly includes `amount` in the comparison. This function is the sole deposit-limit gate; no other check in `depositETH` → `_beforeDeposit` compensates for the missing `amount`.

## Impact Explanation
The deposit limit is the protocol's primary risk-management cap on ETH exposure routed into EigenLayer strategies. Bypassing it breaks the invariant `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset[ETH]`. No direct fund loss or freeze occurs, but the protocol silently accepts more ETH than governance intended. This matches the allowed impact: **Low — Contract fails to deliver its promised deposit-cap guarantee for ETH**.

## Likelihood Explanation
The condition is trivially reachable by any unprivileged depositor. No special role, timing window, oracle manipulation, or governance action is required. Once `getTotalAssetDeposits(ETH_TOKEN)` reaches the configured limit (a normal operational state), every subsequent `depositETH()` call bypasses the cap. The bypass is repeatable indefinitely until the limit is raised.

## Recommendation
Add `+ amount` to the ETH branch to match the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. Protocol accumulates exactly `100 ether` in ETH deposits (`getTotalAssetDeposits(ETH_TOKEN) == 100 ether`).
3. `getAssetCurrentLimit(ETH_TOKEN)` returns `0`, signalling no remaining capacity.
4. Attacker calls `depositETH{value: 50 ether}(0, "")`.
5. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 50 ether)` evaluates `100 ether > 100 ether` → `false` → no revert.
6. `_mintRsETH` executes; `getTotalAssetDeposits(ETH_TOKEN)` becomes `150 ether`, 50% above the cap.
7. An equivalent ERC20 deposit at the same state would evaluate `100 ether + 50 ether > 100 ether` → `true` → revert with `MaximumDepositLimitReached`.

**Foundry test sketch:**
```solidity
function test_ethDepositBypassesLimit() public {
    vm.prank(admin);
    lrtConfig.setDepositLimitByAsset(ETH_TOKEN, 100 ether);
    // fill to exactly the limit
    depositor.depositETH{value: 100 ether}(0, "");
    assertEq(pool.getTotalAssetDeposits(ETH_TOKEN), 100 ether);
    // this should revert but does not
    attacker.depositETH{value: 50 ether}(0, "");
    assertGt(pool.getTotalAssetDeposits(ETH_TOKEN), 100 ether); // passes — cap bypassed
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L87-87)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
