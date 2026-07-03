### Title
Single Privileged Operator Controls Withdrawal Unlock with No User Cancel Path — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

After a user calls `LRTWithdrawalManager.initiateWithdrawal()`, their rsETH is immediately transferred into the contract and locked. The only path to recover those funds requires the privileged `OPERATOR_ROLE` (or `ASSET_TRANSFER_ROLE`) to call `unlockQueue()`. There is no user-callable cancel or timeout mechanism. If the operator is offline or selectively censoring a user's request, that user's rsETH is frozen indefinitely.

---

### Finding Description

The withdrawal lifecycle in LRT-rsETH is a multi-step pipeline:

1. **User** calls `initiateWithdrawal()` — rsETH is pulled from the user into `LRTWithdrawalManager` and a `WithdrawalRequest` is recorded.
2. **Operator** calls `NodeDelegator.initiateUnstaking()` (`onlyLRTOperator`) — queues the EigenLayer withdrawal.
3. **Operator** calls `NodeDelegator.completeUnstaking()` (`onlyLRTOperator`) — finalizes the EigenLayer withdrawal and sends assets to `LRTUnstakingVault`.
4. **Operator** calls `LRTWithdrawalManager.unlockQueue()` (`onlyAssetTransferOrOperatorRole`) — advances `nextLockedNonce[asset]` and burns the held rsETH.
5. **User** calls `completeWithdrawal()` — succeeds only if `usersFirstWithdrawalRequestNonce < nextLockedNonce[asset]`.

Step 1 is the point of no return for the user: rsETH leaves their wallet immediately. [1](#0-0) 

Steps 2–4 are all exclusively operator-gated. `unlockQueue` is the on-chain gate that advances `nextLockedNonce`: [2](#0-1) 

`completeWithdrawal` hard-reverts with `WithdrawalLocked` if the operator has not yet called `unlockQueue` for the user's nonce: [3](#0-2) 

`initiateUnstaking` and `completeUnstaking` on `NodeDelegator` are both `onlyLRTOperator`: [4](#0-3) [5](#0-4) 

There is no `cancelWithdrawal`, no expiry timestamp, and no fallback path that allows a user to reclaim their rsETH from a pending request without operator cooperation. A grep across all production contracts confirms no such function exists.

---

### Impact Explanation

A user who calls `initiateWithdrawal()` immediately loses custody of their rsETH. If the operator is offline, compromised, or selectively censoring that user's nonce, the rsETH remains locked in `LRTWithdrawalManager` with no on-chain recourse. The user cannot burn, transfer, or recover it. This constitutes a **temporary (potentially extended) freezing of user funds**, matching the Medium impact tier: *Temporary freezing of funds*.

---

### Likelihood Explanation

The `OPERATOR_ROLE` is a single off-chain key or bot. Operational incidents (key loss, infrastructure failure, bot bugs, or deliberate censorship of specific addresses) are realistic. The protocol already acknowledges operator centrality through the `onlyLRTOperator` guard on every critical unstaking step. The absence of any user-side escape hatch makes the exposure window unbounded.

---

### Recommendation

1. Add a `cancelWithdrawal(address asset)` function callable by the user after a configurable deadline (e.g., `withdrawalStartBlock + maxWaitBlocks`) that returns the locked rsETH to the user and removes the request.
2. Alternatively, allow any caller (not just the operator) to call `unlockQueue` once the withdrawal delay has passed and assets are available in the vault, removing the single-point-of-failure.

---

### Proof of Concept

1. Alice calls `initiateWithdrawal(stETH, 1e18, "")`. Her 1 rsETH is transferred to `LRTWithdrawalManager`; `nextUnusedNonce[stETH]` becomes `N+1`; `nextLockedNonce[stETH]` remains `N`. [6](#0-5) 

2. The operator goes offline (or ignores Alice). `unlockQueue` is never called. `nextLockedNonce[stETH]` stays at `N`.

3. Alice calls `completeWithdrawal(stETH, "")`. Inside `_processWithdrawalCompletion`, the check `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]` evaluates to `N >= N` → `true` → reverts `WithdrawalLocked`. [3](#0-2) 

4. Alice has no other on-chain function to call. Her rsETH is frozen in `LRTWithdrawalManager` for as long as the operator remains inactive or censoring.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-178)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/NodeDelegator.sol (L293-302)
```text
    function initiateUnstaking(
        IStrategy[] calldata strategies,
        uint256[] calldata shares
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlyLRTOperator
        returns (bytes32 withdrawalRoot)
```

**File:** contracts/NodeDelegator.sol (L346-354)
```text
    function completeUnstaking(
        IDelegationManager.Withdrawal calldata withdrawal,
        IERC20[] calldata assets,
        bool receiveAsTokens
    )
        public
        nonReentrant
        whenNotPaused
        onlyLRTOperator
```
