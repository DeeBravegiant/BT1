Audit Report

## Title
Uninitialized `maxMintAmountPerDay` In `RSETH::reinitialize` Causes `RSETH::mint` To Always Revert - (File: contracts/RSETH.sol)

## Summary
The `reinitialize` function in `RSETH.sol` sets `periodStartTime` and `custodyAddress` but never initializes `maxMintAmountPerDay`, leaving it at its default value of `0`. Because the `checkDailyMintLimit` modifier applied to every `mint` call reverts when `currentPeriodMintedAmount + amount > maxMintAmountPerDay`, any non-zero mint immediately reverts post-upgrade. All user deposits are blocked until an admin separately calls `setMaxMintAmountPerDay`.

## Finding Description
`RSETH.reinitialize` (lines 109–117) accepts `_periodStartTime` and `_custodyAddress` and writes them to storage, but never assigns `maxMintAmountPerDay`:

```solidity
function reinitialize(uint256 _periodStartTime, address _custodyAddress)
    external reinitializer(2) onlyLRTManager
{
    ...
    periodStartTime = _periodStartTime;
    _setCustodyAddress(_custodyAddress);
    // maxMintAmountPerDay is never set → remains 0
}
```

The `checkDailyMintLimit` modifier (lines 42–56) guards every `mint` call:

```solidity
if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
    revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
}
```

With `maxMintAmountPerDay == 0`, the condition reduces to `amount > 0`, which is always true for any real deposit. The `mint` function (lines 229–240) carries this modifier unconditionally. `LRTDepositPool._mintRsETH` (line 689) calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)` for every `depositAsset` and `depositETH` call, so the entire deposit path is blocked. The separate `setMaxMintAmountPerDay` function (lines 125–128) exists but is not called atomically during `reinitialize`.

## Impact Explanation
Every user deposit into the LRT protocol (via `LRTDepositPool.depositAsset` or `depositETH`) triggers `RSETH.mint`. With `maxMintAmountPerDay == 0`, every such call reverts with `DailyMintLimitExceeded(amount, 0)`. User funds are not permanently lost, but the entire deposit path is inoperative from the moment the upgrade is applied until an admin calls `setMaxMintAmountPerDay`. This constitutes **temporary freezing of funds**, a Medium-severity impact within the allowed scope.

## Likelihood Explanation
The broken state is the default post-upgrade state — no attacker action is required. Any unprivileged user who attempts a deposit after the upgrade (but before the admin remediation) will have their transaction revert. The window of impact is bounded only by how quickly the admin notices and calls `setMaxMintAmountPerDay`. No special capability, front-running, or external dependency is needed.

## Recommendation
Add `_maxMintAmountPerDay` as a parameter to `reinitialize` and assign it atomically:

```diff
function reinitialize(
    uint256 _periodStartTime,
-   address _custodyAddress
+   address _custodyAddress,
+   uint256 _maxMintAmountPerDay
) external reinitializer(2) onlyLRTManager {
    if (_periodStartTime > block.timestamp || _periodStartTime <= block.timestamp - 1 days) {
        revert PeriodStartTimeShouldBeWithin24Hours();
    }
    periodStartTime = _periodStartTime;
    emit PeriodStartTimeSet(_periodStartTime);
    _setCustodyAddress(_custodyAddress);
+   maxMintAmountPerDay = _maxMintAmountPerDay;
+   emit MaxMintAmountPerDayUpdated(_maxMintAmountPerDay);
}
```

## Proof of Concept
1. Protocol upgrades `RSETH` to v2 and calls `reinitialize(_periodStartTime, _custodyAddress)`.
2. `maxMintAmountPerDay` is never written; it remains `0` (confirmed at `contracts/RSETH.sol` line 19).
3. A user calls `LRTDepositPool.depositAsset(asset, amount, minRSETH, "")`.
4. `LRTDepositPool._mintRsETH` (line 689) calls `RSETH.mint(user, rsethAmountToMint)`.
5. `checkDailyMintLimit(rsethAmountToMint)` evaluates `0 + rsethAmountToMint > 0` → `true`.
6. Transaction reverts with `DailyMintLimitExceeded(rsethAmountToMint, 0)`.
7. All deposits are blocked until `setMaxMintAmountPerDay` is called by an admin.

Foundry test sketch:
```solidity
function test_reinitialize_blocks_mint() public {
    // upgrade and call reinitialize without setting maxMintAmountPerDay
    rseth.reinitialize(block.timestamp - 1 hours, custodyAddress);
    assertEq(rseth.maxMintAmountPerDay(), 0);

    // any mint attempt reverts
    vm.prank(minterRole);
    vm.expectRevert(
        abi.encodeWithSelector(RSETH.DailyMintLimitExceeded.selector, 1 ether, 0)
    );
    rseth.mint(user, 1 ether);
}
```