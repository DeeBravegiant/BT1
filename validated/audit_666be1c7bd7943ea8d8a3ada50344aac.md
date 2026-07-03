### Title
Replacing `LRT_WITHDRAW_MANAGER` in `LRTConfig` Freezes All In-Flight User Withdrawals - (File: contracts/LRTConfig.sol)

### Summary
`LRTConfig.setContract()` allows the `DEFAULT_ADMIN_ROLE` to replace any registered contract address at any time, including `LRT_WITHDRAW_MANAGER`. Because `LRTUnstakingVault.redeem()` gates access via `onlyLRTWithdrawalManager` — which dynamically resolves the current `LRT_WITHDRAW_MANAGER` from `LRTConfig` — replacing that address immediately revokes the old `LRTWithdrawalManager`'s ability to pull assets from the vault. All users who have already called `initiateWithdrawal()` and deposited rsETH into the old manager are left with their rsETH frozen there, unable to complete or cancel their withdrawals.

### Finding Description

`LRTConfig.setContract()` is an unrestricted setter for any entry in `contractMap`:

```solidity
// contracts/LRTConfig.sol:237-251
function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
    _setContract(contractKey, contractAddress);
}
function _setContract(bytes32 key, address val) private {
    UtilLib.checkNonZeroAddress(val);
    if (contractMap[key] == val) { revert ValueAlreadyInUse(); }
    contractMap[key] = val;
    emit SetContract(key, val);
}
``` [1](#0-0) 

This includes the `LRT_WITHDRAW_MANAGER` key. The `LRTUnstakingVault` enforces that only the currently-registered withdrawal manager may call `redeem()`:

```solidity
// contracts/LRTUnstakingVault.sol:54-58
modifier onlyLRTWithdrawalManager() {
    if (msg.sender != lrtConfig.withdrawManager()) {
        revert CallerNotLRTWithdrawalManager();
    }
    _;
}
``` [2](#0-1) 

`lrtConfig.withdrawManager()` resolves dynamically to `contractMap[LRT_WITHDRAW_MANAGER]` at call time. [3](#0-2) 

The withdrawal lifecycle is:

1. User calls `LRTWithdrawalManager.initiateWithdrawal()` → rsETH is pulled from the user and held inside the withdrawal manager; a `WithdrawalRequest` is recorded. [4](#0-3) 

2. Operator calls `unlockQueue()` → the withdrawal manager calls `unstakingVault.redeem()` to pull assets from the vault. [5](#0-4) 

3. User calls `completeWithdrawal()` → assets are transferred to the user. [6](#0-5) 

If the admin calls `setContract(LRT_WITHDRAW_MANAGER, newAddress)` while users have pending (locked) withdrawal requests in the old manager:

- The old `LRTWithdrawalManager` can no longer call `unstakingVault.redeem()` — the vault's `onlyLRTWithdrawalManager` modifier now resolves to `newAddress`, so every call from the old manager reverts with `CallerNotLRTWithdrawalManager`.
- `unlockQueue()` on the old manager is permanently broken.
- All rsETH deposited by users into the old manager via `initiateWithdrawal()` is frozen there with no withdrawal path. [7](#0-6) 

### Impact Explanation

All users who called `initiateWithdrawal()` before the address change have their rsETH frozen in the old `LRTWithdrawalManager`. They cannot complete withdrawals (the unlock step is broken) and there is no cancel/refund path in the contract. The rsETH remains stuck until the admin restores the old address — matching the original report's characterization of "temporary freeze" (funds are not permanently lost if the old address is restored, but users have no recourse on their own).

**Impact: Medium — Temporary freezing of user funds.**

### Likelihood Explanation

The admin legitimately needs to upgrade the withdrawal manager (e.g., to deploy a new version with bug fixes or new features). There is no migration mechanism or guard preventing this action while withdrawals are in flight. Any upgrade to the withdrawal manager contract while users have pending requests triggers this freeze. This is a realistic operational scenario.

### Recommendation

1. Before allowing `setContract(LRT_WITHDRAW_MANAGER, ...)`, require that `nextLockedNonce[asset] == nextUnusedNonce[asset]` for all assets (i.e., no pending withdrawal requests exist in the old manager).
2. Alternatively, implement a migration function that transfers all pending withdrawal state and held rsETH from the old manager to the new one atomically before the address is switched.
3. Add a `cancelWithdrawal()` function so users can reclaim their rsETH if the system is being migrated.

### Proof of Concept

1. Alice calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 1e18, "")`. Her 1 rsETH is transferred to the old withdrawal manager; a `WithdrawalRequest` is stored.
2. Admin calls `LRTConfig.setContract(LRT_WITHDRAW_MANAGER, newManagerAddress)`.
3. Operator calls `oldWithdrawalManager.unlockQueue(stETH, ...)`. Inside, it calls `unstakingVault.redeem(stETH, amount)`. The vault's `onlyLRTWithdrawalManager` modifier checks `msg.sender != lrtConfig.withdrawManager()` → `oldManager != newManagerAddress` → reverts with `CallerNotLRTWithdrawalManager`.
4. Alice's rsETH is permanently stuck in the old withdrawal manager. She has no function to call to recover it. [1](#0-0) [2](#0-1) [8](#0-7) [9](#0-8)

### Citations

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

**File:** contracts/LRTUnstakingVault.sol (L54-59)
```text
    modifier onlyLRTWithdrawalManager() {
        if (msg.sender != lrtConfig.withdrawManager()) {
            revert CallerNotLRTWithdrawalManager();
        }
        _;
    }
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

**File:** contracts/utils/LRTConstants.sol (L93-95)
```text
    function withdrawManager(ILRTConfig config) internal view returns (address) {
        return config.getContract(LRT_WITHDRAW_MANAGER);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L283-307)
```text
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
