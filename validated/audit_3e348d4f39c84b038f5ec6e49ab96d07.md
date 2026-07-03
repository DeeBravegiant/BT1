### Title
rsETH Permanently Burned Before Vault Availability Check in `instantWithdrawal` — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`instantWithdrawal` in `LRTWithdrawalManager` burns the caller's rsETH **before** checking whether the `LRTUnstakingVault` holds enough assets to cover the redemption. If the post-burn availability check reverts, the user's rsETH is permanently destroyed with no asset payout.

---

### Finding Description

In `LRTWithdrawalManager.instantWithdrawal`, the execution order is:

1. Compute `assetAmountUnlocked` from the oracle price.
2. **Burn** the caller's rsETH irreversibly.
3. **Then** check whether the vault can cover `assetAmountUnlocked`.
4. Revert if the vault cannot — but the burn at step 2 is already committed. [1](#0-0) 

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);   // ← irreversible
ILRTUnstakingVault unstakingVault = ...;
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();   // ← reverts AFTER burn
}
```

`getAssetsAvailableForInstantWithdrawal` returns `vaultBalance - queuedWithdrawalsBuffer`, which can be zero or less than `assetAmountUnlocked` at execution time. [2](#0-1) 

The check-before-burn pattern is the correct design (used correctly in `initiateWithdrawal` which checks `getAvailableAssetAmount` before transferring rsETH). Here the order is inverted. [3](#0-2) 

---

### Impact Explanation

Any user whose `instantWithdrawal` transaction hits the `CantInstantWithdrawMoreThanAvailable` revert loses their rsETH permanently — the burn is not rolled back. This is a direct, permanent loss of user funds with no recovery path inside the protocol.

Impact: **Critical — permanent theft/freezing of user funds.**

---

### Likelihood Explanation

The condition `assetAmountUnlocked > getAssetsAvailableForInstantWithdrawal(asset)` can be triggered by:

1. **Race condition / front-run**: Two users simultaneously observe sufficient vault liquidity and both submit `instantWithdrawal`. The second transaction to execute will pass the balance check at read time but fail after the first transaction drains the vault. The second user's rsETH is burned with no payout.
2. **Operator buffer increase**: An operator calls `setQueuedWithdrawalsBuffer` to raise `queuedWithdrawalsBuffer[asset]` between the user's off-chain check and on-chain execution, reducing `availableAmount` to below `assetAmountUnlocked`. [4](#0-3) 

Instant withdrawal is a live, user-facing feature gated only by `isInstantWithdrawalEnabled[asset]`. No admin compromise is required for the race-condition path.

---

### Recommendation

Move the availability check **before** the burn, mirroring the safe pattern in `initiateWithdrawal`:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
// Check BEFORE burning
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
unstakingVault.redeem(asset, assetAmountUnlocked);
```

---

### Proof of Concept

1. Vault holds 10 ETH; `queuedWithdrawalsBuffer[ETH] = 0`; instant withdrawal is enabled.
2. Alice and Bob each hold rsETH worth 10 ETH and both call `instantWithdrawal` in the same block.
3. Alice's tx executes first: rsETH burned, vault drained to 0 ETH, Alice receives 10 ETH. ✓
4. Bob's tx executes second:
   - Line 228: `assetAmountUnlocked = 10 ETH`
   - Line 229: `burnFrom(Bob, rsETHUnstaked)` — Bob's rsETH is **permanently destroyed**.
   - Line 231: `10 ETH > 0 ETH` → `revert CantInstantWithdrawMoreThanAvailable()`.
5. Bob has lost his rsETH and received nothing. The revert does not undo the burn. [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-170)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

**File:** contracts/LRTWithdrawalManager.sol (L228-233)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }
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

**File:** contracts/LRTUnstakingVault.sol (L235-238)
```text
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
```
