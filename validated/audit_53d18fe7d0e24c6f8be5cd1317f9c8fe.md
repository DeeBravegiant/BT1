Audit Report

## Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Deposits Beyond the Configured Cap - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses a strict `>` comparison without adding the incoming `amount` for the ETH branch, while the ERC-20 branch correctly adds `amount` before comparing. When `totalAssetDeposits` exactly equals the configured cap, the ETH check returns `false` and the deposit proceeds, silently breaching the limit. Any unprivileged caller can exploit this once the cap is reached.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682, the function branches on asset type:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
``` [1](#0-0) 

The ETH branch evaluates `totalAssetDeposits > limit`. When `totalAssetDeposits == limit` (the cap is exactly reached), this returns `false`, so `_beforeDeposit` at line 661 does not revert with `MaximumDepositLimitReached`. [2](#0-1) 

The call then proceeds through `depositETH` at line 87–90, minting rsETH for the full `msg.value` and pushing `totalAssetDeposits` to `limit + msg.value`. [3](#0-2) 

`getAssetCurrentLimit` at lines 402–408 already returns `0` at this state (using the same `>` without `amount`), so off-chain tooling correctly reports the cap as reached while the contract still accepts deposits — a direct inconsistency. [4](#0-3) 

No other guard exists in the deposit path that would catch this.

## Impact Explanation
The deposit limit (`depositLimitByAsset`) is the protocol's primary on-chain risk-management cap. Bypassing it allows the protocol to accumulate more ETH than the governance-approved ceiling, violating the promised cap. No funds are directly stolen and no yield is diverted; the protocol simply fails to enforce its stated deposit ceiling. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value**, which is within the allowed impact scope.

## Likelihood Explanation
The triggering condition (`totalAssetDeposits == depositLimit`) is a routine operational state — the cap being reached. Any depositor monitoring on-chain state can observe this via `getAssetCurrentLimit` returning `0` and immediately call `depositETH` with any `msg.value > 0`. No special role, privileged access, timing window, or front-running is required. The exploit is repeatable for every subsequent deposit until governance raises the limit. Likelihood is **High**.

## Recommendation
Mirror the ERC-20 logic in the ETH branch by including `amount` in the comparison:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [5](#0-4) 

## Proof of Concept
1. Admin calls `lrtConfig.setDepositLimitByAsset(ETH_TOKEN, X)`.
2. Legitimate deposits accumulate until `getTotalAssetDeposits(ETH_TOKEN) == X`.
3. Confirm: `getAssetCurrentLimit(ETH_TOKEN)` returns `0`.
4. Attacker calls `depositETH{value: Y}(0, "")` for any `Y > 0`.
5. Inside `_beforeDeposit`, `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, Y)` evaluates `X > X` → `false` → no revert.
6. `_mintRsETH` mints rsETH for `Y` ETH; `getTotalAssetDeposits(ETH_TOKEN)` is now `X + Y`, exceeding the cap.

**Foundry test sketch:**
```solidity
function test_ethDepositBypassesLimit() public {
    uint256 limit = 10 ether;
    vm.prank(admin);
    lrtConfig.setDepositLimitByAsset(LRTConstants.ETH_TOKEN, limit);

    // Fill to exactly the limit via legitimate deposits
    _fillETHDepositsToLimit(limit);
    assertEq(depositPool.getAssetCurrentLimit(LRTConstants.ETH_TOKEN), 0);

    // Attacker deposits beyond the cap — should revert but doesn't
    vm.deal(attacker, 1 ether);
    vm.prank(attacker);
    depositPool.depositETH{value: 1 ether}(0, "");

    assertGt(depositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN), limit);
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L87-90)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
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
