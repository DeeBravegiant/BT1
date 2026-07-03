Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing Amount in Cap Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` omits the incoming `amount` from the ETH branch of the cap check, testing only `totalAssetDeposits > depositLimit` instead of `totalAssetDeposits + amount > depositLimit`. Any unprivileged depositor can push total ETH holdings arbitrarily past the configured `depositLimitByAsset` ceiling in a single transaction. The LST branch at line 681 performs the correct inclusive check, confirming the ETH branch is an oversight.

## Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682 of `contracts/LRTDepositPool.sol`):

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct for LSTs
}
```

The ETH branch returns `true` only when the pre-deposit total already exceeds the cap. It never adds the incoming `msg.value` to the comparison. When `totalAssetDeposits == depositLimit - 1 wei`, the check returns `false` and the deposit proceeds for any `msg.value`, minting rsETH and pushing the real total to `depositLimit - 1 + msg.value`.

The function is called unconditionally from `_beforeDeposit` (line 661), which is the sole pre-flight guard for `depositETH`. `depositETH` (lines 76–93) is a public, permissionless, payable entry point that passes `msg.value` as `depositAmount` directly to `_beforeDeposit`. No existing check compensates for the missing `amount` in the ETH branch.

## Impact Explanation
`depositLimitByAsset` is the protocol's risk-management ceiling for each accepted asset. Bypassing it for ETH causes the protocol to mint rsETH beyond the intended cap, accepting ETH it cannot deploy to EigenLayer strategies. The surplus ETH sits idle and un-yielding while rsETH holders expect full yield, meaning the contract fails to deliver its promised returns. This matches the allowed impact: **Low — Contract fails to deliver promised returns**.

## Likelihood Explanation
`depositETH` is public and requires no special role or privilege. Any depositor who observes `getTotalAssetDeposits(ETH_TOKEN)` approaching `depositLimitByAsset[ETH_TOKEN]` can call `depositETH{value: <large amount>}` in a single transaction. No front-running, governance access, or privileged key is needed. The condition is trivially reachable whenever the ETH deposit limit is close to being filled, and the exploit is repeatable as long as the limit is not administratively raised.

## Recommendation
Add the incoming `amount` to the ETH branch of the cap check, mirroring the LST logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This aligns the ETH path with the LST path and with `getAssetCurrentLimit` (lines 402–409), which already computes remaining capacity as `depositLimit - totalAssetDeposits` without any special ETH exception.

## Proof of Concept
Assume `depositLimitByAsset[ETH_TOKEN] = 100 ether` and `getTotalAssetDeposits(ETH_TOKEN)` returns `99.9 ether`.

1. Attacker calls `depositETH{value: 50 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 50 ether)`.
3. ETH branch evaluates `99.9 ether > 100 ether` → `false` → limit not exceeded.
4. `_mintRsETH` mints rsETH for 50 ETH; total ETH in protocol becomes `149.9 ether`, exceeding the 100 ETH cap by 49.9 ETH.
5. The equivalent LST call would evaluate `99.9 + 50 > 100` → `true` → revert with `MaximumDepositLimitReached`.

**Foundry test plan:** Deploy `LRTDepositPool` with `depositLimitByAsset[ETH_TOKEN] = 100 ether`, seed `99.9 ether` of prior deposits, call `depositETH{value: 50 ether}`, assert `getTotalAssetDeposits(ETH_TOKEN) > 100 ether` and that no revert occurred. Confirm the patched version reverts under identical conditions.