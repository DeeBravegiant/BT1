Audit Report

## Title
Deposit-limit saturation permanently blocks LST processing in L1VaultV2 with no recovery path — (`contracts/L1VaultV2.sol`)

## Summary
`depositAssetForL1Vault` is the sole function for moving LST tokens out of `L1VaultV2` into `LRTDepositPool`. It performs no pre-check against the per-asset deposit limit before calling `lrtDepositPool.depositAsset`, which reverts with `MaximumDepositLimitReached` when the limit is saturated. The contract contains no rescue, sweep, or emergency-withdrawal function, so LSTs bridged from L2 accumulate in the vault with no processing path until an admin raises the limit via `LRTConfig.updateAssetDepositLimit`.

## Finding Description
`depositAssetForL1Vault` (L240–256 of `contracts/L1VaultV2.sol`) reads the full token balance, computes an rsETH mint amount via `getRsETHAmountToMint` (a pure oracle calculation that ignores the deposit limit), approves the pool, then calls `lrtDepositPool.depositAsset`. The actual limit check occurs inside `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` (`contracts/LRTDepositPool.sol` L661–663, L676–682), which reverts the entire transaction when `totalAssetDeposits + amount > depositLimitByAsset[asset]`. Because the revert is atomic, the `safeIncreaseAllowance` is also rolled back — no allowance accumulates. The structural problem is that `L1VaultV2` has no alternative egress: a full-text search for `rescue`, `recover`, `emergencyWithdraw`, `transferToken`, and `sweep` returns zero matches in the contract. The only resolution is an out-of-band admin call to `LRTConfig.updateAssetDepositLimit`.

Exploit path:
1. Public L1 depositors saturate `depositLimitByAsset[stETH]` (e.g., 100,000 ether).
2. Independently, L2 users deposit stETH on L2; the bridge delivers stETH to `L1VaultV2`.
3. Manager calls `depositAssetForL1Vault(stETH)` → `_beforeDeposit` reverts with `MaximumDepositLimitReached`.
4. stETH balance remains in `L1VaultV2`; no rsETH is minted for L2 users.
5. No function on `L1VaultV2` can move the tokens out; freeze persists until admin raises the limit.

## Impact Explanation
LST tokens bridged from L2 are temporarily frozen in `L1VaultV2`: they cannot be deposited into `LRTDepositPool` (limit reached), cannot be transferred out (no rescue function), and cannot be partially deposited (function always uses full balance). This matches **Medium — Temporary freezing of funds**. The freeze is bounded: it lifts once an admin raises `depositLimitByAsset` via `LRTConfig.updateAssetDepositLimit`, but requires out-of-band admin intervention with no on-chain self-resolution.

## Likelihood Explanation
Deposit limits are a live protocol parameter (initialized at 100,000 ether per asset). The L2→L1 bridge pipeline operates independently of the deposit pool's remaining capacity. Public L1 depositors can saturate the limit without any privileged action. No on-chain guard in `L1VaultV2` or the bridge prevents LSTs from arriving when the pool is at capacity. The scenario is a natural operational state requiring no adversarial coordination.

## Recommendation
1. **Pre-check the deposit limit** before attempting the deposit in `depositAssetForL1Vault`:
```solidity
uint256 currentLimit = lrtDepositPool.getAssetCurrentLimit(token);
if (currentLimit < tokenBalance) revert DepositLimitReached();
```
2. **Add an emergency token-rescue function** gated to `TIMELOCK_ROLE` so LSTs can be recovered if the deposit path is blocked:
```solidity
function rescueToken(address token, address to, uint256 amount)
    external onlyRole(TIMELOCK_ROLE) {
    IERC20(token).safeTransfer(to, amount);
}
```

## Proof of Concept
```solidity
// Foundry fork test
// 1. Deploy LRTConfig, LRTDepositPool, L1VaultV2 with stETH supported.
// 2. Set depositLimitByAsset[stETH] = 100 ether.
// 3. Simulate prior deposits so getTotalAssetDeposits(stETH) == 100 ether (limit saturated).
// 4. Transfer 10 ether of stETH directly into L1VaultV2 (simulating bridge delivery).
// 5. Call depositAssetForL1Vault(stETH) as manager.
// 6. Assert: call reverts with MaximumDepositLimitReached.
// 7. Assert: stETH balance of L1VaultV2 is still 10 ether (tokens stuck).
// 8. Assert: no function on L1VaultV2 moves the tokens out.
// 9. Raise depositLimitByAsset[stETH] to 200 ether (admin action).
// 10. Call depositAssetForL1Vault(stETH) again — succeeds.
//     Confirms freeze is temporary but requires out-of-band admin intervention.
```