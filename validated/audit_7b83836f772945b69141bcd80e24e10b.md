Audit Report

## Title
ETH Deposit Limit Check Omits Incoming Amount, Allowing Limit Bypass — (File: `contracts/LRTDepositPool.sol`)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses `totalAssetDeposits > depositLimit` for ETH but `totalAssetDeposits + amount > depositLimit` for all other assets. Because the incoming ETH amount is never added before comparison, the deposit cap for ETH is never enforced against a deposit that would push the total over the limit. Any unprivileged caller can bypass the ETH TVL cap via `depositETH`.

## Finding Description
`depositETH` (L76–93) is a fully permissionless `payable` function that calls `_beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected)`. `_beforeDeposit` (L661–663) calls `_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)` and reverts with `MaximumDepositLimitReached` only if it returns `true`.

Inside `_checkIfDepositAmountExceedesCurrentLimit` (L676–682):

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount not included
}
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

For ETH, only the pre-deposit total is compared against the limit. The function returns `false` (no revert) for any ETH deposit as long as the current total has not already exceeded the limit, regardless of the deposit size. The non-ETH branch correctly includes `amount`. The asymmetry means the ETH cap is structurally unenforced.

## Impact Explanation
The ETH deposit limit is a protocol-stated safety invariant bounding total ETH exposure. With this bug the invariant is broken: a single `depositETH` call can push total ETH holdings from just below the limit to an arbitrarily large value. No funds are directly stolen, but the contract fails to deliver its promised safety guarantee (bounded ETH deposits). This maps to **Low — contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
`depositETH` requires no role, no special timing, and no privileged access. The vulnerable condition (total deposits near but below the limit) is the normal operating state of a live protocol. Any external address can trigger it with a single transaction.

## Recommendation
Add the incoming deposit amount to the ETH branch to match the non-ETH branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether`.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 ether > 1000 ether` → `false` → no revert.
5. Deposit proceeds; total ETH becomes `1499 ether`, exceeding the cap by `499 ether`.
6. An equivalent `depositAsset` call with any LST evaluates `999 ether + 500 ether > 1000 ether` → `true` → `revert MaximumDepositLimitReached()`.

Foundry test plan: deploy with a mock `lrtConfig` returning `depositLimitByAsset = 1000 ether`, seed the pool to `999 ether` total, call `depositETH{value: 500 ether}`, assert the call succeeds and `getTotalAssetDeposits` returns `1499 ether`; then assert an equivalent `depositAsset` call reverts.