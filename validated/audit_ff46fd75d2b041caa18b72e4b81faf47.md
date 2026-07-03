Audit Report

## Title
No Rate Limit on `instantWithdrawal` Enables Vault Buffer Drain DoS - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.instantWithdrawal` has no per-user cap, rate limit, or queue. An unprivileged attacker can deposit ETH into `LRTDepositPool` to obtain rsETH, then immediately call `instantWithdrawal` to drain the `LRTUnstakingVault`'s available instant-withdrawal balance. Once the balance reaches zero, every subsequent `instantWithdrawal` call reverts, forcing all users onto the queued withdrawal path with an 8-day delay. At the default `instantWithdrawalFee` of 0, the attack costs only gas.

## Finding Description
`instantWithdrawal` (lines 212–253) burns the caller's rsETH and redeems the corresponding asset from `LRTUnstakingVault`. The only guard against over-withdrawal is a balance check against `getAssetsAvailableForInstantWithdrawal`:

```solidity
// contracts/LRTWithdrawalManager.sol L228-235
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
ILRTUnstakingVault unstakingVault = ...;
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
```

`getAssetsAvailableForInstantWithdrawal` returns `vaultBalance - queuedWithdrawalsBuffer[asset]`, which reaches zero once the attacker drains the non-reserved portion.

The attack path is:
1. Attacker calls `LRTDepositPool.depositETH{value: X}()` → receives Y rsETH. The deposited ETH lands in `LRTDepositPool` (and is eventually forwarded to EigenLayer via node delegators); it does **not** replenish `LRTUnstakingVault`.
2. Attacker calls `LRTWithdrawalManager.instantWithdrawal(ETH_TOKEN, Y, "")` → burns Y rsETH, receives ≈ X ETH from `LRTUnstakingVault` (minus `instantWithdrawalFee`, which defaults to 0).
3. Attacker repeats until `getAssetsAvailableForInstantWithdrawal` returns 0.

After step 3, every legitimate `instantWithdrawal` call reverts with `CantInstantWithdrawMoreThanAvailable`. The vault is only replenished when operators complete EigenLayer unstaking cycles (multi-day process). The `isInstantWithdrawalEnabled[asset]` toggle is an admin-controlled on/off switch, not a rate-limiting mechanism; once the feature is live, no per-call or per-user restriction exists.

`instantWithdrawalFee` is a `uint256` storage variable with no initializer, so it defaults to 0. Even if set to the maximum of 1000 bps (10%), the attacker recovers 90% of capital per cycle, making sustained draining economically viable.

## Impact Explanation
Once the vault's available balance is exhausted, all `instantWithdrawal` calls revert. Users holding rsETH who expected immediate liquidity are forced onto `initiateWithdrawal` → `completeWithdrawal`, which enforces `withdrawalDelayBlocks = 8 days / 12 seconds`. Because vault replenishment is operator-driven and slow, and the attacker can re-drain immediately after any replenishment at near-zero cost, the freeze can be sustained indefinitely. This constitutes **Medium — Temporary freezing of funds**: user rsETH is not lost, but access to liquidity is blocked for an extended and attacker-controlled period.

## Likelihood Explanation
The entry path (`depositETH` → `instantWithdrawal`) is fully permissionless. The attacker needs ETH capital proportional to the vault's available balance but recovers it each cycle (minus fee + gas). At `instantWithdrawalFee = 0` (the Solidity default), the only cost is gas. No privileged access, no complex exploit chain, and no victim mistake is required. The attack is repeatable on every block.

## Recommendation
1. **Enforce a non-zero minimum `instantWithdrawalFee`** at initialization to raise the per-cycle economic cost.
2. **Add a per-address cooldown** (e.g., `lastInstantWithdrawal[msg.sender]` timestamp check) between successive `instantWithdrawal` calls from the same address.
3. **Add a per-block or per-epoch withdrawal cap** on the total amount redeemable via `instantWithdrawal` to bound the rate at which the vault can be drained.
4. **Consider a short-priority queue** for instant withdrawals: if the vault balance is insufficient, queue the request rather than reverting, so legitimate users are not forced to compete or retry.

## Proof of Concept
```solidity
// Foundry fork test outline
function test_drainInstantWithdrawalVault() external {
    // Precondition: instant withdrawal enabled for ETH, vault has X ETH available
    uint256 available = unstakingVault.getAssetsAvailableForInstantWithdrawal(ETH_TOKEN);

    vm.startPrank(attacker);
    // Step 1: deposit ETH into pool, receive rsETH
    depositPool.depositETH{value: available}(0, "");
    uint256 rsETHBal = rsETH.balanceOf(attacker);

    // Step 2: drain vault via instant withdrawal (fee = 0 by default)
    withdrawalManager.instantWithdrawal(ETH_TOKEN, rsETHBal, "");
    vm.stopPrank();

    // Vault available balance is now 0
    assertEq(unstakingVault.getAssetsAvailableForInstantWithdrawal(ETH_TOKEN), 0);

    // Step 3: legitimate user is blocked
    vm.prank(victim);
    vm.expectRevert(ILRTWithdrawalManager.CantInstantWithdrawMoreThanAvailable.selector);
    withdrawalManager.instantWithdrawal(ETH_TOKEN, victimRsETH, "");
}
```