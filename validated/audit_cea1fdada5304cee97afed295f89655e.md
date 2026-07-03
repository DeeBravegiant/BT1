### Title
`LRTConfig.setContract()` Strands User Withdrawal Funds When `LRT_UNSTAKING_VAULT` Is Replaced Without Draining Old Vault - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.setContract()` allows the admin to overwrite the `LRT_UNSTAKING_VAULT` address in `contractMap` with no check on whether the old vault still holds user funds or has pending withdrawal obligations. After the swap, `LRTWithdrawalManager.unlockQueue()` resolves the vault address at call-time from `lrtConfig`, so it reads the new (empty) vault, sees zero balance, and reverts. All users who have already initiated withdrawals have their rsETH locked inside `LRTWithdrawalManager` with no path to completion.

---

### Finding Description

`LRTConfig._setContract()` is a generic setter that overwrites any entry in `contractMap` with no precondition checks:

```solidity
// contracts/LRTConfig.sol  lines 244-251
function _setContract(bytes32 key, address val) private {
    UtilLib.checkNonZeroAddress(val);
    if (contractMap[key] == val) {
        revert ValueAlreadyInUse();
    }
    contractMap[key] = val;   // ← no balance / pending-withdrawal check
    emit SetContract(key, val);
}
``` [1](#0-0) 

Contrast this with `updateAssetStrategy()`, which explicitly iterates every NDC and reverts if the old strategy still holds funds:

```solidity
// contracts/LRTConfig.sol  lines 151-166
if (assetStrategy[asset] != address(0)) {
    ...
    uint256 ndcBalance = IStrategy(assetStrategy[asset]).userUnderlyingView(ndcs[i]);
    if (ndcBalance > 0) {
        revert CannotUpdateStrategyAsItHasFundsNDCFunds(ndcs[i], ndcBalance);
    }
}
``` [2](#0-1) 

No equivalent guard exists for `LRT_UNSTAKING_VAULT`.

`LRTWithdrawalManager.unlockQueue()` resolves the vault address dynamically at call-time:

```solidity
// contracts/LRTWithdrawalManager.sol  lines 283-284
ILRTUnstakingVault unstakingVault =
    ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
``` [3](#0-2) 

`_createUnlockParams()` then queries `balanceOf` on that vault:

```solidity
// contracts/LRTWithdrawalManager.sol  lines 846-850
return UnlockParams({
    rsETHPrice: lrtOracle.rsETHPrice(),
    assetPrice: lrtOracle.getAssetPrice(asset),
    totalAvailableAssets: unstakingVault.balanceOf(asset)   // ← new vault, balance = 0
});
``` [4](#0-3) 

Because the new vault has zero balance, `unlockQueue` immediately reverts:

```solidity
// contracts/LRTWithdrawalManager.sol  line 297
if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
``` [5](#0-4) 

The old vault's funds are stranded: `LRTUnstakingVault.redeem()` is gated by `onlyLRTWithdrawalManager`, so only the withdrawal manager can pull funds out, but the withdrawal manager now points to the new vault. [6](#0-5) 

---

### Impact Explanation

Users who called `initiateWithdrawal()` transferred their rsETH into `LRTWithdrawalManager`:

```solidity
// contracts/LRTWithdrawalManager.sol  line 166
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
``` [7](#0-6) 

After the vault swap, `unlockQueue` always reverts (zero balance in new vault), so `nextLockedNonce` is never advanced, and `completeWithdrawal` always reverts with `WithdrawalLocked`. The rsETH is frozen inside the withdrawal manager with no user-accessible exit path. This is a **temporary (potentially permanent) freezing of user funds** — Medium impact per the allowed scope.

---

### Likelihood Explanation

The scenario is a routine protocol upgrade: the team deploys a new `LRTUnstakingVault` implementation and calls `setContract(LRT_UNSTAKING_VAULT, newVault)` before draining the old vault. No malicious intent is required; the missing safety check makes this a realistic operational mistake. The `updateAssetStrategy` function in the same file demonstrates the team is aware of this pattern for strategies but omitted it for the vault setter.

---

### Recommendation

Add a balance check inside `_setContract` (or a dedicated `setUnstakingVault` function) that mirrors the guard already present in `updateAssetStrategy`:

```solidity
if (key == LRTConstants.LRT_UNSTAKING_VAULT && contractMap[key] != address(0)) {
    ILRTUnstakingVault oldVault = ILRTUnstakingVault(contractMap[key]);
    address[] memory assets = ILRTConfig(address(this)).getSupportedAssetList();
    for (uint256 i; i < assets.length; i++) {
        if (oldVault.balanceOf(assets[i]) > 0) revert OldVaultStillHasFunds();
    }
    // also check ETH
    if (oldVault.balanceOf(LRTConstants.ETH_TOKEN) > 0) revert OldVaultStillHasFunds();
}
```

Alternatively, provide a dedicated migration path that atomically drains the old vault into the new one before updating the pointer.

---

### Proof of Concept

1. Users call `LRTWithdrawalManager.initiateWithdrawal(stETH, amount, "")`. Their rsETH is held in `LRTWithdrawalManager`; `assetsCommitted[stETH]` is incremented.
2. Operator transfers stETH to `LRTUnstakingVault` (old) via `LRTDepositPool.transferAssetToLRTUnstakingVault`.
3. Admin deploys `LRTUnstakingVaultV2` and calls `LRTConfig.setContract(LRT_UNSTAKING_VAULT, newVaultAddr)`.
4. Operator calls `LRTWithdrawalManager.unlockQueue(stETH, ...)`.
   - Line 284: `unstakingVault` resolves to `newVaultAddr`.
   - Line 849: `unstakingVault.balanceOf(stETH)` returns `0`.
   - Line 297: reverts with `AmountMustBeGreaterThanZero()`.
5. `nextLockedNonce[stETH]` is never advanced. All users calling `completeWithdrawal` revert with `WithdrawalLocked`. Their rsETH remains frozen in `LRTWithdrawalManager`. The stETH in the old vault is inaccessible because `redeem()` is `onlyLRTWithdrawalManager` and the withdrawal manager now points to the new vault. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/LRTConfig.sol (L151-166)
```text
        if (assetStrategy[asset] != address(0)) {
            // get ndcs
            address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);
            address[] memory ndcs = ILRTDepositPool(depositPool).getNodeDelegatorQueue();

            uint256 length = ndcs.length;
            for (uint256 i = 0; i < length;) {
                uint256 ndcBalance = IStrategy(assetStrategy[asset]).userUnderlyingView(ndcs[i]);
                if (ndcBalance > 0) {
                    revert CannotUpdateStrategyAsItHasFundsNDCFunds(ndcs[i], ndcBalance);
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** contracts/LRTConfig.sol (L237-251)
```text
    function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setContract(contractKey, contractAddress);
    }

    /// @dev private function to set a contract
    /// @param key Contract key
    /// @param val Contract address
    function _setContract(bytes32 key, address val) private {
        UtilLib.checkNonZeroAddress(val);
        if (contractMap[key] == val) {
            revert ValueAlreadyInUse();
        }
        contractMap[key] = val;
        emit SetContract(key, val);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L268-307)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
    {
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTUnstakingVault.sol (L99-105)
```text
    function redeem(address asset, uint256 amount) external nonReentrant onlyLRTWithdrawalManager {
        if (asset == LRTConstants.ETH_TOKEN) {
            ILRTWithdrawalManager(msg.sender).receiveFromLRTUnstakingVault{ value: amount }();
        } else {
            IERC20(asset).safeTransfer(msg.sender, amount);
        }
    }
```
