Audit Report

## Title
`instantWithdrawal` Bypasses `assetsCommitted` Protection, Enabling Temporary Freeze of Queued Withdrawals — (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.instantWithdrawal` redeems assets from `LRTUnstakingVault` without ever decrementing or consulting `assetsCommitted[asset]`. The only guard is `queuedWithdrawalsBuffer`, which is a Solidity mapping that defaults to `0` and has no protocol-level enforcement requiring it to be set before instant withdrawals are enabled. When the buffer is `0`, any rsETH holder can drain the entire vault, causing all subsequent `unlockQueue` calls to revert and freezing queued withdrawal users until the operator manually replenishes the vault.

## Finding Description
Two independent accounting mechanisms exist and are never synchronized:

**Mechanism 1 — `assetsCommitted` in `LRTWithdrawalManager`:**
`initiateWithdrawal` adds the expected asset amount to `assetsCommitted[asset]` (line 173). `getAvailableAssetAmount` uses this to prevent over-commitment (lines 599–602). However, `instantWithdrawal` (lines 212–253) never reads or modifies `assetsCommitted[asset]` at any point.

**Mechanism 2 — `queuedWithdrawalsBuffer` in `LRTUnstakingVault`:**
`getAssetsAvailableForInstantWithdrawal` (lines 229–238) computes `vaultBalance - queuedWithdrawalsBuffer[asset]`. Since `queuedWithdrawalsBuffer` is a mapping, it defaults to `0`. `setQueuedWithdrawalsBuffer` (lines 199–208) is a separate operator action with no link to `assetsCommitted`.

**Exploit path:**
1. Alice calls `initiateWithdrawal(ETH, 100e18, "")` → `assetsCommitted[ETH] = 100e18`.
2. Operator moves 100 ETH into `LRTUnstakingVault` to prepare for `unlockQueue`.
3. Manager calls `setInstantWithdrawalEnabled(ETH, true)`. `queuedWithdrawalsBuffer[ETH]` remains `0` (default).
4. Attacker (any rsETH holder) calls `instantWithdrawal(ETH, X, "")` sized to drain the vault. `getAssetsAvailableForInstantWithdrawal` returns `100 ETH - 0 = 100 ETH`. The call succeeds and removes all 100 ETH.
5. Operator calls `unlockQueue(ETH, ...)`. `_createUnlockParams` reads `unstakingVault.balanceOf(asset)` (line 849), which is now `0`. The check at line 297 — `if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()` — reverts.
6. Alice's queued withdrawal is frozen. `assetsCommitted[ETH]` still equals `100e18` but there are no vault assets to unlock against.

**Why existing checks fail:**
- `instantWithdrawal` checks only `getAssetsAvailableForInstantWithdrawal`, which is blind to `assetsCommitted`.
- There is no check in `setInstantWithdrawalEnabled` requiring `queuedWithdrawalsBuffer >= assetsCommitted`.
- `unlockQueue` reads the live vault balance directly with no fallback.

## Impact Explanation
**Medium — Temporary freezing of funds.** Users who called `initiateWithdrawal` and are waiting for `unlockQueue` cannot progress their withdrawal. The freeze persists until the operator manually replenishes the vault. Funds are not permanently lost, but user withdrawals are blocked for an indefinite period, matching the "Temporary freezing of funds" impact class.

## Likelihood Explanation
- `isInstantWithdrawalEnabled` being set to `true` is a normal operational decision when the instant withdrawal feature is deployed.
- `queuedWithdrawalsBuffer` defaults to `0` and requires a separate, explicit operator action to set. No protocol-level enforcement links it to `assetsCommitted`. In practice, the buffer will frequently be `0` or stale.
- The attacker only needs to hold rsETH — any legitimate user qualifies.
- No privileged access, front-running, or external dependency is required beyond the operator's routine enabling of instant withdrawals.

Likelihood: **Medium**.

## Recommendation
1. In `instantWithdrawal`, cap the redeemable amount as `min(getAssetsAvailableForInstantWithdrawal(asset), totalVaultBalance - assetsCommitted[asset])` so committed queued withdrawals are always protected regardless of the buffer setting.
2. Alternatively, derive `queuedWithdrawalsBuffer` dynamically from `assetsCommitted` rather than relying on a manually set static value.
3. Add a precondition in `setInstantWithdrawalEnabled` that requires `queuedWithdrawalsBuffer[asset] >= assetsCommitted[asset]` before enabling instant withdrawals for an asset.

## Proof of Concept
```
// Foundry fork test outline
function test_instantWithdrawalDrainsVaultFreezingQueuedWithdrawals() public {
    // 1. Alice initiates a queued withdrawal
    vm.prank(alice);
    withdrawalManager.initiateWithdrawal(ETH, 100e18, "");
    // assetsCommitted[ETH] == 100e18

    // 2. Operator funds the unstaking vault
    vm.deal(address(unstakingVault), 100 ether);

    // 3. Manager enables instant withdrawals (buffer stays 0 by default)
    vm.prank(manager);
    withdrawalManager.setInstantWithdrawalEnabled(ETH, true);
    // queuedWithdrawalsBuffer[ETH] == 0

    // 4. Attacker drains the vault via instantWithdrawal
    // getAssetsAvailableForInstantWithdrawal returns 100 ETH (100 - 0)
    vm.prank(attacker); // attacker holds rsETH
    withdrawalManager.instantWithdrawal(ETH, attackerRsETHAmount, "");
    assertEq(address(unstakingVault).balance, 0);

    // 5. unlockQueue reverts — Alice's withdrawal is frozen
    vm.prank(operator);
    vm.expectRevert(AmountMustBeGreaterThanZero.selector);
    withdrawalManager.unlockQueue(ETH, type(uint256).max, 0, 0, type(uint256).max, type(uint256).max);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTUnstakingVault.sol (L199-208)
```text
    function setQueuedWithdrawalsBuffer(
        address asset,
        uint256 buffer
    )
        external
        onlyLRTOperator
        onlySupportedAsset(asset)
    {
        queuedWithdrawalsBuffer[asset] = buffer;
        emit QueuedWithdrawalsBufferUpdated(asset, buffer);
```

**File:** contracts/LRTUnstakingVault.sol (L229-238)
```text
    function getAssetsAvailableForInstantWithdrawal(address asset)
        external
        view
        onlySupportedAsset(asset)
        returns (uint256 availableAmount)
    {
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
```
