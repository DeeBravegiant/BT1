### Title
Instant Withdrawals Can Drain Vault Assets Reserved for Queued Withdrawal Requests, Temporarily Freezing Queued Users' Funds - (File: contracts/LRTWithdrawalManager.sol, contracts/LRTUnstakingVault.sol)

---

### Summary

The `LRTUnstakingVault` uses a static, manually-set `queuedWithdrawalsBuffer` to protect vault assets from being consumed by instant withdrawals. However, this buffer is not automatically synchronized with `assetsCommitted` in `LRTWithdrawalManager`, which dynamically tracks assets reserved for pending queued withdrawal requests. An unprivileged user with rsETH can call `instantWithdrawal` to drain vault assets before `unlockQueue` is called, leaving queued withdrawal users unable to complete their withdrawals and their rsETH permanently locked in the contract until the vault is manually refilled.

---

### Finding Description

The vulnerability class from H03 is: a dual-role entity can spend funds that are supposed to be locked/reserved for another purpose, because the lock is not enforced at the spending path. The analog in LRT-rsETH is that `assetsCommitted` (the accounting lock for queued withdrawal requests) is not enforced at the `instantWithdrawal` spending path.

**Root cause — two independent accounting systems with no cross-enforcement:**

**1. `assetsCommitted` in `LRTWithdrawalManager`** tracks assets reserved for pending queued withdrawal requests across the entire protocol: [1](#0-0) 

When a user calls `initiateWithdrawal`, `assetsCommitted[asset]` is incremented by `expectedAssetAmount`: [2](#0-1) 

**2. `queuedWithdrawalsBuffer` in `LRTUnstakingVault`** is the only mechanism protecting vault assets from instant withdrawals: [3](#0-2) 

`getAssetsAvailableForInstantWithdrawal` returns `vaultBalance - reservedBuffer`: [4](#0-3) 

**3. `instantWithdrawal` only checks the vault buffer, never `assetsCommitted`:** [5](#0-4) 

**4. `queuedWithdrawalsBuffer` defaults to zero** and is set manually by the operator with no enforcement that it must be ≥ the portion of `assetsCommitted` currently held in the vault: [6](#0-5) 

**5. `unlockQueue` uses the full vault balance** (`unstakingVault.balanceOf(asset)`) as `totalAvailableAssets`, so if the vault is drained by instant withdrawals, it reverts with `AmountMustBeGreaterThanZero` and no queued requests can be unlocked: [7](#0-6) [8](#0-7) 

---

### Impact Explanation

When assets arrive in `LRTUnstakingVault` (e.g., from `NodeDelegator.completeUnstaking` after EigenLayer's delay), and `queuedWithdrawalsBuffer` is 0 (the default), an attacker can call `instantWithdrawal` to drain the entire vault balance. This prevents `unlockQueue` from executing, leaving queued withdrawal users with their rsETH locked inside `LRTWithdrawalManager` (transferred in `initiateWithdrawal` but not yet burned) and their expected assets inaccessible. There is no cancellation path for queued withdrawal requests. The attacker can sustain this by front-running every vault refill, making the freeze persistent until the operator sets a non-zero buffer.

**Impact: Medium — Temporary (potentially sustained) freezing of funds for queued withdrawal users.**

---

### Likelihood Explanation

- Instant withdrawal must be enabled by the manager (`isInstantWithdrawalEnabled[asset] = true`), which is a normal operational decision.
- The vault balance is publicly visible on-chain; an attacker can monitor it and call `instantWithdrawal` immediately when assets arrive.
- `queuedWithdrawalsBuffer` defaults to 0 and requires a separate operator transaction to set; there is a window between vault funding and buffer configuration.
- The attacker only needs rsETH tokens, which are freely obtainable by depositing into the protocol.

**Likelihood: Medium.**

---

### Recommendation

Replace the static `queuedWithdrawalsBuffer` with a dynamic check that enforces the invariant `instantWithdrawal` cannot consume assets that are already committed to queued withdrawal requests. Specifically, `getAssetsAvailableForInstantWithdrawal` should subtract `assetsCommitted[asset]` (or the portion of it held in the vault) from the available balance, rather than relying on a manually-maintained buffer. Alternatively, automatically update `queuedWithdrawalsBuffer` whenever `assetsCommitted` changes, or add a cross-contract check in `instantWithdrawal` that verifies the vault will retain sufficient assets to service all pending queued requests.

---

### Proof of Concept

1. Protocol has 100 ETH in EigenLayer. User A calls `initiateWithdrawal(ETH, rsETH_A)` → `assetsCommitted[ETH] = 10 ETH`, rsETH_A locked in `LRTWithdrawalManager`.
2. Operator completes EigenLayer withdrawal → 10 ETH arrives in `LRTUnstakingVault`. `queuedWithdrawalsBuffer[ETH] = 0` (default).
3. Attacker calls `instantWithdrawal(ETH, rsETH_B)` where `rsETH_B` corresponds to 10 ETH. Check: `getAssetsAvailableForInstantWithdrawal(ETH) = 10 - 0 = 10 ETH`. Passes. Vault is drained.
4. Operator calls `unlockQueue(ETH, ...)`. `_createUnlockParams` reads `unstakingVault.balanceOf(ETH) = 0`. Function reverts at `if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()`.
5. User A's rsETH remains locked in `LRTWithdrawalManager`. User A cannot complete withdrawal. Attacker repeats step 3 on every vault refill. [9](#0-8) [10](#0-9) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L52-53)
```text
    // Asset amount committed to be withdrawn by users.
    mapping(address asset => uint256 amount) public assetsCommitted;
```

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L283-297)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L837-851)
```text
    function _createUnlockParams(
        ILRTOracle lrtOracle,
        ILRTUnstakingVault unstakingVault,
        address asset
    )
        internal
        view
        returns (UnlockParams memory)
    {
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```

**File:** contracts/LRTUnstakingVault.sol (L42-43)
```text
    // Portion of the vault reserved for servicing queued withdrawals; unavailable for instant withdrawals.
    mapping(address asset => uint256 buffer) public queuedWithdrawalsBuffer;
```

**File:** contracts/LRTUnstakingVault.sol (L196-209)
```text
    /// @notice Set the reserved buffer for queued withdrawals for an asset.
    /// @param asset The asset address.
    /// @param buffer The reserved amount for queued withdrawals.
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
    }
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
