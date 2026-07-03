### Title
Missing `assetsCommitted` Enforcement in `instantWithdrawal()` Allows Draining of Vault Assets Reserved for Queued Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal()` checks available vault assets using `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal()`, which relies solely on the manually-set `queuedWithdrawalsBuffer`. It does not check against `assetsCommitted[asset]`, the accounting variable that tracks assets already promised to users who have initiated queued withdrawals. Because `queuedWithdrawalsBuffer` defaults to zero and is not automatically synchronized with `assetsCommitted`, any rsETH holder can drain vault assets that are committed to pending queued withdrawal requests, causing `unlockQueue()` to revert and temporarily freezing queued withdrawers' funds.

---

### Finding Description

`initiateWithdrawal()` records a user's claim by incrementing `assetsCommitted[asset]` and locking their rsETH in the contract. The committed assets remain in `LRTUnstakingVault` until an operator calls `unlockQueue()`, which pulls them out and burns the corresponding rsETH. [1](#0-0) 

`instantWithdrawal()` takes a completely separate path: it calls `unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)`, which computes availability as `vaultBalance - queuedWithdrawalsBuffer[asset]`. [2](#0-1) 

`queuedWithdrawalsBuffer` is a manually-set operator variable that defaults to zero for every asset. [3](#0-2) [4](#0-3) 

When `queuedWithdrawalsBuffer[asset] == 0`, `getAssetsAvailableForInstantWithdrawal` returns the full vault balance, regardless of how much is committed via `assetsCommitted`. [5](#0-4) 

`instantWithdrawal()` never reads `assetsCommitted[asset]` and therefore has no awareness of the committed-but-not-yet-unlocked queued withdrawal obligations. [6](#0-5) 

When `unlockQueue()` is subsequently called, it reads `unstakingVault.balanceOf(asset)` as the available asset pool. If instant withdrawals have drained the vault, this returns zero and the function reverts immediately. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

Queued withdrawal users who called `initiateWithdrawal()` have their rsETH locked in the contract and are waiting for `unlockQueue()` to process their requests. If the vault is drained by instant withdrawers before `unlockQueue()` runs, the operator call reverts with `AmountMustBeGreaterThanZero`, and no queued requests can be unlocked. Users cannot recover their rsETH or receive their assets until operators manually replenish the vault by moving assets from the deposit pool or EigenLayer. This constitutes a **temporary freezing of funds** for queued withdrawal users.

---

### Likelihood Explanation

`queuedWithdrawalsBuffer` defaults to zero for all assets and must be manually set by an operator. Any rsETH holder with a balance meeting `minRsEthAmountToWithdraw[asset]` can call `instantWithdrawal()` when `isInstantWithdrawalEnabled[asset]` is true. No special role or privilege is required. The attacker does not need to front-run; they simply need to call `instantWithdrawal()` before `unlockQueue()` is executed. The window between `initiateWithdrawal()` and `unlockQueue()` is at least `withdrawalDelayBlocks` (~8 days), giving ample time to exploit. [9](#0-8) 

---

### Recommendation

`instantWithdrawal()` should enforce that the amount being withdrawn does not exceed `vaultBalance - assetsCommitted[asset]` (capped at zero), rather than relying solely on the manually-set `queuedWithdrawalsBuffer`. Alternatively, `getAssetsAvailableForInstantWithdrawal()` in `LRTUnstakingVault` should be updated to accept and incorporate the committed amount from the withdrawal manager, or `assetsCommitted` should automatically update `queuedWithdrawalsBuffer` whenever a queued withdrawal is initiated or completed.

---

### Proof of Concept

1. Alice calls `initiateWithdrawal(ETH, 10e18, "")`. `assetsCommitted[ETH] = 10e18`. Her rsETH is locked. The vault holds 10 ETH.
2. `queuedWithdrawalsBuffer[ETH]` is 0 (default). `getAssetsAvailableForInstantWithdrawal(ETH)` returns `10e18`.
3. Bob calls `instantWithdrawal(ETH, rsETHFor10ETH, "")`. The check `assetAmountUnlocked > getAssetsAvailableForInstantWithdrawal(ETH)` passes. The vault is drained to 0.
4. Operator calls `unlockQueue(ETH, ...)`. `_createUnlockParams` reads `unstakingVault.balanceOf(ETH) = 0`. The function reverts at `if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()`.
5. Alice cannot complete her withdrawal. Her rsETH remains locked in the contract until operators replenish the vault. [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L170-173)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L296-297)
```text

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTUnstakingVault.sol (L43-43)
```text
    mapping(address asset => uint256 buffer) public queuedWithdrawalsBuffer;
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
