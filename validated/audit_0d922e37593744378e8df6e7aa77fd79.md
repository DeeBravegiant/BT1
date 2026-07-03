### Title
Users Who Initiate Withdrawals Cannot Cancel or Recover Locked rsETH Without Operator Cooperation - (File: `contracts/LRTWithdrawalManager.sol`)

### Summary
`LRTWithdrawalManager.initiateWithdrawal()` transfers a user's rsETH into the contract and creates a locked withdrawal request, but no `cancelWithdrawal` function exists. The only path to completion requires a privileged operator to first call `unlockQueue()`, which advances `nextLockedNonce` and unlocks the request. If the operator never acts, the user's rsETH is permanently trapped in the contract with no on-chain recovery path.

### Finding Description
When a user calls `initiateWithdrawal()`, their rsETH is immediately transferred from their wallet into `LRTWithdrawalManager`:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
``` [1](#0-0) 

The request is then stored in a locked state. The user can only call `completeWithdrawal()` after the request has been unlocked, which is enforced by:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
``` [2](#0-1) 

`nextLockedNonce` is only advanced inside `unlockQueue()`, which is gated to `onlyAssetTransferOrOperatorRole`:

```solidity
function unlockQueue(...) external nonReentrant onlySupportedAsset(asset) whenNotPaused onlyAssetTransferOrOperatorRole
``` [3](#0-2) 

There is no `cancelWithdrawal` function anywhere in the contract. The alternative path, `instantWithdrawal()`, is also gated behind `isInstantWithdrawalEnabled[asset]`, which is set exclusively by the manager:

```solidity
function setInstantWithdrawalEnabled(address asset, bool enabled) external onlySupportedAsset(asset) onlyLRTManager
``` [4](#0-3) 

The rsETH is not burned at initiation — it sits in the contract — but the user has no on-chain mechanism to reclaim it. The only burn path is inside `unlockQueue()` itself:

```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
``` [5](#0-4) 

### Impact Explanation
A user who calls `initiateWithdrawal()` immediately loses custody of their rsETH. If the operator (`ASSET_TRANSFER_ROLE` / `OPERATOR_ROLE`) does not call `unlockQueue()` — due to technical failure, operational delay, or any other reason — the user's rsETH is frozen in `LRTWithdrawalManager` indefinitely. There is no on-chain escape hatch. This constitutes **temporary freezing of funds** (Medium) with a realistic path to **permanent freezing** (Critical) if the operator role becomes permanently unavailable. The user cannot even re-deposit or trade the locked rsETH since it is no longer in their wallet. [6](#0-5) 

### Likelihood Explanation
Any user who calls `initiateWithdrawal()` is immediately exposed. The operator is expected to call `unlockQueue()` regularly, but there is no on-chain time-bound guarantee or deadline enforced in the contract. The `withdrawalDelayBlocks` parameter (default ~8 days) only governs the minimum wait after unlocking — it does not force the operator to unlock. A single period of operator unavailability (infrastructure outage, key rotation failure, governance dispute) is sufficient to freeze all pending withdrawal rsETH. [7](#0-6) 

### Recommendation
Add a `cancelWithdrawal(address asset)` function callable by the original depositor that:
1. Checks the request is still in the locked state (`usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]`).
2. Removes the request from `userAssociatedNonces` and `withdrawalRequests`.
3. Decrements `assetsCommitted[asset]` by the committed amount.
4. Returns the rsETH to `msg.sender` via `safeTransfer`.

This mirrors the fix recommended in the external report: expose the exit logic directly to the user so they are not permanently dependent on a privileged role.

### Proof of Concept
1. Alice holds 10 rsETH and calls `initiateWithdrawal(ETH_TOKEN, 10e18, "")`.
2. `LRTWithdrawalManager` receives Alice's 10 rsETH; her wallet balance is now 0.
3. The operator (`ASSET_TRANSFER_ROLE`) goes offline indefinitely (key loss, infrastructure failure, etc.).
4. `unlockQueue()` is never called; `nextLockedNonce[ETH_TOKEN]` remains at its prior value.
5. Alice calls `completeWithdrawal(ETH_TOKEN, "")` → reverts with `WithdrawalLocked`.
6. Alice has no `cancelWithdrawal` to call. Her 10 rsETH remains locked in `LRTWithdrawalManager` with no recovery path.
7. `instantWithdrawal` is also unavailable unless the manager separately enables it via `setInstantWithdrawalEnabled`. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
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

**File:** contracts/LRTWithdrawalManager.sol (L268-281)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

**File:** contracts/LRTWithdrawalManager.sol (L360-367)
```text
    function setInstantWithdrawalEnabled(address asset, bool enabled)
        external
        onlySupportedAsset(asset)
        onlyLRTManager
    {
        isInstantWithdrawalEnabled[asset] = enabled;
        emit InstantWithdrawalEnabledUpdated(asset, enabled);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L700-707)
```text
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```
