### Title
Replacing `LRT_WITHDRAW_MANAGER` via `LRTConfig.setContract()` Permanently Freezes Users' rsETH and Pending Withdrawals - (`contracts/LRTConfig.sol`)

---

### Summary

`LRTConfig.setContract()` allows the admin to atomically replace the registered `LRT_WITHDRAW_MANAGER` address with no checks on pending withdrawal state. After replacement, the old `LRTWithdrawalManager` holds users' rsETH (transferred in during `initiateWithdrawal`) and all pending withdrawal queue state, but is immediately cut off from `LRTUnstakingVault.redeem()` by the `onlyLRTWithdrawalManager` access guard. There is no migration path, no cancellation mechanism, and no way for users to recover their locked rsETH from the old contract.

---

### Finding Description

`LRTConfig.setContract()` is a general-purpose admin function that updates any entry in `contractMap`:

```solidity
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

There are **no guards** checking whether the old withdrawal manager holds pending user funds or queued withdrawal requests before the replacement takes effect. Compare this to `updateAssetStrategy()`, which explicitly iterates all NodeDelegators and reverts if any strategy still holds funds: [2](#0-1) 

When a user calls `initiateWithdrawal()`, their rsETH is transferred **into the withdrawal manager contract itself**:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
``` [3](#0-2) 

The withdrawal request state (`withdrawalRequests`, `userAssociatedNonces`, `nextUnusedNonce`, `nextLockedNonce`, `assetsCommitted`) is also stored in the old contract's storage. [4](#0-3) 

`LRTUnstakingVault.redeem()` — the function the withdrawal manager must call to pull assets out and pay users — is gated by:

```solidity
modifier onlyLRTWithdrawalManager() {
    if (msg.sender != lrtConfig.withdrawManager()) {
        revert CallerNotLRTWithdrawalManager();
    }
    _;
}
``` [5](#0-4) 

`lrtConfig.withdrawManager()` reads live from `contractMap`, so the instant `setContract(LRT_WITHDRAW_MANAGER, newAddress)` executes, the old withdrawal manager's calls to `redeem()` revert. Both `unlockQueue()` and `completeWithdrawal()` depend on this call path: [6](#0-5) 

The old `LRTWithdrawalManager` exposes no function to return rsETH to users, cancel pending requests, or transfer its token balance elsewhere. The only asset-exit function is `sweepRemainingAssets()`, which requires `hasUnlockedWithdrawals(asset) == false` and sends funds to the treasury — not to users — and does not handle the locked rsETH held for pending requests. [7](#0-6) 

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

Every user who called `initiateWithdrawal()` before the contract replacement has their rsETH permanently locked in the old withdrawal manager with no on-chain recovery path. The old contract cannot call `redeem()` on the vault (access revoked), cannot burn rsETH (no mechanism), and cannot return rsETH to users (no cancellation function). All pending withdrawal queue state is also orphaned. The new withdrawal manager starts with a blank slate and knows nothing about prior requests.

---

### Likelihood Explanation

Moderate. Deploying a new `LRTWithdrawalManager` and pointing `LRTConfig` to it is a natural and expected protocol upgrade operation (the contract already has `initialize2`, `initialize3` reinitializers showing a history of upgrades). The admin has no on-chain signal warning them that `setContract` will freeze active user funds — unlike `updateAssetStrategy`, which enforces a zero-balance precondition. Any upgrade performed while the withdrawal queue is non-empty triggers the freeze.

---

### Recommendation

1. Add a precondition guard to `setContract` (or a dedicated `setWithdrawalManager` function) that reverts if the current withdrawal manager holds any pending rsETH or has non-zero `nextUnusedNonce` minus completed nonces — mirroring the pattern already used in `updateAssetStrategy`.
2. Add a `cancelWithdrawal()` function to `LRTWithdrawalManager` that allows users to reclaim their rsETH before a migration, or an admin-callable `migrateWithdrawals()` that transfers rsETH balances and queue state to the new contract.
3. Alternatively, enforce that `setContract(LRT_WITHDRAW_MANAGER, ...)` can only be called when `nextUnusedNonce[asset] == nextLockedNonce[asset]` for all assets (i.e., no pending requests exist).

---

### Proof of Concept

1. Alice calls `initiateWithdrawal(stETH, 1e18, "")`. The old `LRTWithdrawalManager` receives 1e18 rsETH and records her request at nonce 0.
2. Admin calls `lrtConfig.setContract(LRTConstants.LRT_WITHDRAW_MANAGER, address(newWithdrawalManager))`.
3. Operator calls `oldWithdrawalManager.unlockQueue(stETH, ...)`. Inside, `unstakingVault.redeem(stETH, amount)` is called. The vault's `onlyLRTWithdrawalManager` modifier checks `msg.sender != lrtConfig.withdrawManager()` → `oldWithdrawalManager != newWithdrawalManager` → **reverts**.
4. Alice calls `oldWithdrawalManager.completeWithdrawal(stETH, "")`. Same revert path — `_processWithdrawalCompletion` calls `_transferAsset` which requires the unlock step to have succeeded first, and `unlockQueue` is permanently broken.
5. Alice's 1e18 rsETH sits in `oldWithdrawalManager` with no on-chain exit. `sweepRemainingAssets` cannot be called because `hasUnlockedWithdrawals` is false (the request was never unlocked), and even if it could be called, it sends to the treasury, not to Alice.

### Citations

**File:** contracts/LRTConfig.sol (L151-167)
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

**File:** contracts/LRTWithdrawalManager.sol (L35-58)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
    uint256 public withdrawalDelayBlocks;

    // Next available nonce for withdrawal requests per asset, indicating total requests made.
    mapping(address asset => uint256 nonce) public nextUnusedNonce;

    // Next nonce for which a withdrawal request remains locked.
    mapping(address asset => uint256 requestNonce) public nextLockedNonce;

    // Mapping from a unique request identifier to its corresponding withdrawal request
    mapping(bytes32 requestId => WithdrawalRequest) public withdrawalRequests;

    // Maps each asset to user addresses, pointing to an ordered list of their withdrawal request nonces.
    // Utilizes a double-ended queue for efficient management and removal of initial requests.
    mapping(address asset => mapping(address user => DoubleEndedQueue.Uint256Deque requestNonces)) public
        userAssociatedNonces;

    // Asset amount committed to be withdrawn by users.
    mapping(address asset => uint256 amount) public assetsCommitted;

    mapping(address asset => bool) public isInstantWithdrawalEnabled;
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)

    mapping(address asset => uint256) public unlockedWithdrawalsCount;
```

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
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

**File:** contracts/LRTWithdrawalManager.sol (L395-414)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
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
