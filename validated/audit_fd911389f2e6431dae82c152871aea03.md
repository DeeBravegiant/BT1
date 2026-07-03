Audit Report

## Title
Static `queuedWithdrawalsBuffer` Not Synchronized with `assetsCommitted` Allows Instant Withdrawals to Drain Vault Assets Reserved for Queued Requests - (File: contracts/LRTUnstakingVault.sol, contracts/LRTWithdrawalManager.sol)

## Summary

`LRTUnstakingVault` uses a static, manually-configured `queuedWithdrawalsBuffer` (defaulting to zero) as the sole guard preventing instant withdrawals from consuming vault assets. This buffer is entirely decoupled from `assetsCommitted` in `LRTWithdrawalManager`, which dynamically tracks assets already promised to pending queued withdrawal requests. An unprivileged rsETH holder can call `instantWithdrawal` to drain the vault when the buffer is zero, causing `unlockQueue` to revert and leaving queued users' rsETH permanently locked in `LRTWithdrawalManager` until the vault is manually refilled.

## Finding Description

**Two independent accounting systems with no cross-enforcement:**

`assetsCommitted[asset]` in `LRTWithdrawalManager` is incremented at `initiateWithdrawal` time to track assets already promised to pending queued requests: [1](#0-0) [2](#0-1) 

The rsETH is also transferred from the user into `LRTWithdrawalManager` at this point, locking it: [3](#0-2) 

`queuedWithdrawalsBuffer` in `LRTUnstakingVault` is the only mechanism protecting vault assets from instant withdrawals. It defaults to zero and is set manually by an operator with no enforcement that it must be ≥ the portion of `assetsCommitted` currently held in the vault: [4](#0-3) [5](#0-4) 

`getAssetsAvailableForInstantWithdrawal` returns `vaultBalance - reservedBuffer`, which equals the full vault balance when the buffer is zero: [6](#0-5) 

`instantWithdrawal` checks only this vault buffer — it never consults `assetsCommitted`: [7](#0-6) 

`_createUnlockParams` reads `unstakingVault.balanceOf(asset)` as `totalAvailableAssets`, so if the vault is drained, `unlockQueue` reverts at the zero-check: [8](#0-7) [9](#0-8) 

There is no cancellation path for queued withdrawal requests in `LRTWithdrawalManager`. Once rsETH is transferred in at `initiateWithdrawal`, it can only be recovered via `completeWithdrawal`, which requires a prior successful `unlockQueue`.

## Impact Explanation

Queued withdrawal users have their rsETH locked inside `LRTWithdrawalManager` with no way to retrieve it until `unlockQueue` succeeds. If an attacker front-runs every vault refill with `instantWithdrawal` calls (spending their own rsETH but receiving fair-value assets in return), the freeze can be sustained indefinitely until the operator sets a non-zero `queuedWithdrawalsBuffer`. This constitutes **temporary (potentially sustained) freezing of funds** for queued withdrawal users — a valid Medium impact per the allowed scope.

## Likelihood Explanation

- `isInstantWithdrawalEnabled[asset]` must be `true`, which is a normal operational state for the protocol.
- `queuedWithdrawalsBuffer` defaults to zero; there is a window between vault funding and buffer configuration during which the attack is possible.
- The attacker only needs rsETH, freely obtainable by depositing into the protocol.
- Vault balance is publicly visible on-chain; the attacker can monitor and call `instantWithdrawal` immediately when assets arrive from EigenLayer.
- The attacker recovers fair-value assets (minus fee) for their rsETH, so the cost of the attack is only the instant withdrawal fee, making repeated front-running economically feasible.

**Likelihood: Medium.**

## Recommendation

Replace the static `queuedWithdrawalsBuffer` with a dynamic check. `getAssetsAvailableForInstantWithdrawal` should subtract `ILRTWithdrawalManager(withdrawalManager).assetsCommitted(asset)` (or the vault-held portion thereof) from the available balance, rather than relying on a manually-maintained buffer. Alternatively, automatically update `queuedWithdrawalsBuffer` whenever `assetsCommitted` changes (e.g., in `initiateWithdrawal` and `_unlockWithdrawalRequests`), or add a cross-contract check in `instantWithdrawal` that verifies the vault will retain sufficient assets to service all pending queued requests after the withdrawal.

## Proof of Concept

1. User A calls `initiateWithdrawal(ETH, rsETH_A)` → `assetsCommitted[ETH] += 10 ETH`; `rsETH_A` is transferred into `LRTWithdrawalManager`.
2. Operator completes EigenLayer withdrawal → 10 ETH arrives in `LRTUnstakingVault`. `queuedWithdrawalsBuffer[ETH] = 0` (default).
3. Attacker calls `instantWithdrawal(ETH, rsETH_B)` where `rsETH_B` corresponds to 10 ETH. Check: `getAssetsAvailableForInstantWithdrawal(ETH) = 10 - 0 = 10 ETH`. Passes. Vault is drained to 0.
4. Operator calls `unlockQueue(ETH, ...)`. `_createUnlockParams` reads `unstakingVault.balanceOf(ETH) = 0`. Function reverts at `if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()`.
5. User A's rsETH remains locked in `LRTWithdrawalManager`. No cancellation path exists. Attacker repeats step 3 on every vault refill, sustaining the freeze until the operator sets a non-zero buffer.

**Foundry test plan:** Deploy `LRTWithdrawalManager` and `LRTUnstakingVault` on a local fork. Enable instant withdrawal for ETH. Have User A call `initiateWithdrawal`. Send 10 ETH to the vault (simulating `NodeDelegator.completeUnstaking`). Have attacker call `instantWithdrawal` for the full vault balance. Assert vault balance is 0. Call `unlockQueue` and assert it reverts with `AmountMustBeGreaterThanZero`. Assert User A's rsETH balance in `LRTWithdrawalManager` is unchanged (still locked).

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L52-53)
```text
    // Asset amount committed to be withdrawn by users.
    mapping(address asset => uint256 amount) public assetsCommitted;
```

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
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

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
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
