### Title
No Emergency Withdrawal Cancellation Mechanism for Permanently Stuck Pending Requests - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` has no user-callable function to cancel a pending withdrawal request and reclaim the deposited rsETH. When a user calls `initiateWithdrawal`, their rsETH is immediately transferred into the contract. Completion requires an operator to first call `unlockQueue`, which advances `nextLockedNonce`. If the queue can never be advanced (e.g., because available assets are permanently zero due to EigenLayer slashing), the rsETH is permanently frozen with no recovery path.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is:

1. **`initiateWithdrawal`** — user's rsETH is pulled into the contract; a `WithdrawalRequest` is stored and the nonce is queued.
2. **`unlockQueue`** (operator-only) — advances `nextLockedNonce[asset]` by processing requests in strict FIFO order; rsETH is burned here.
3. **`completeWithdrawal`** — user receives the underlying asset; requires `usersFirstWithdrawalRequestNonce < nextLockedNonce[asset]`.

The critical gap: **there is no `cancelWithdrawal` function**. Once rsETH enters the contract via `initiateWithdrawal`, the only exit paths are `completeWithdrawal` / `completeWithdrawalForUser`, both of which require the request to have been unlocked first.

`unlockQueue` can be permanently blocked without any admin action:

```solidity
// LRTWithdrawalManager.sol:297
if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

`totalAvailableAssets` is computed as:

```solidity
// LRTWithdrawalManager.sol:601-602
uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
```

If EigenLayer slashing permanently reduces `totalAssets` to zero (or below `assetsCommitted`), `unlockQueue` always reverts. Even if `totalAvailableAssets > 0`, the FIFO loop breaks at the first request whose `payoutAmount` exceeds available assets:

```solidity
// LRTWithdrawalManager.sol:800
if (availableAssetAmount < payoutAmount) break;
```

`nextLockedNonce` is never advanced, so every subsequent user's `completeWithdrawal` call reverts:

```solidity
// LRTWithdrawalManager.sol:707
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

There is no `cancelWithdrawal`, no `emergencyRefund`, and no admin function to return the rsETH held in the contract to the original depositors. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**Permanent freezing of funds (Critical).**

Users who have called `initiateWithdrawal` have already surrendered their rsETH to the contract. If `nextLockedNonce[asset]` can never advance — because `totalAvailableAssets` is permanently zero or the first queued request's payout permanently exceeds available assets — every affected user's rsETH is irretrievably locked. There is no admin escape hatch that returns rsETH to depositors; the only admin function that touches rsETH in the contract is `unlockQueue` (which burns it), not a refund path. [7](#0-6) [8](#0-7) 

---

### Likelihood Explanation

**Low-to-Medium.** EigenLayer slashing that reduces a supported asset's total deposits to zero is a tail-risk event, but it is an explicitly acknowledged risk in the protocol (the `NodeDelegator` integrates directly with EigenLayer strategies). The FIFO queue-blocking scenario (one large request blocking all smaller ones behind it) is more likely and requires no slashing — only a temporary liquidity shortfall that becomes permanent if the asset is later deprecated. The protocol has no on-chain mechanism to handle either case. [9](#0-8) [10](#0-9) 

---

### Recommendation

Add a user-callable `cancelWithdrawal(address asset)` function that:
1. Verifies the request is still in the locked (not yet unlocked) state (`usersFirstNonce >= nextLockedNonce[asset]`).
2. Removes the request from `userAssociatedNonces` and `withdrawalRequests`.
3. Decrements `assetsCommitted[asset]` by `request.expectedAssetAmount`.
4. Returns `request.rsETHUnstaked` of rsETH back to the user via `safeTransfer`.

Optionally, add an admin-callable `emergencyRefundWithdrawals(address asset, address[] users)` that can process refunds when the contract is paused, mirroring the emergency resolver pattern from the referenced report.

---

### Proof of Concept

**Attack Path (EigenLayer slashing scenario):**

1. User calls `initiateWithdrawal(stETH, 10 ether rsETH, "")`. rsETH is transferred to `LRTWithdrawalManager`. `assetsCommitted[stETH] += expectedStETH`.
2. EigenLayer slashing event reduces all stETH held in strategies to zero. `lrtDepositPool.getTotalAssetDeposits(stETH)` returns 0.
3. Operator calls `unlockQueue(stETH, ...)`. Execution hits `if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()` — reverts permanently.
4. User calls `completeWithdrawal(stETH, "")`. Execution hits `if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked()` — reverts permanently.
5. No `cancelWithdrawal` exists. User's 10 ether rsETH is permanently locked in `LRTWithdrawalManager`. [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L268-320)
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

        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }

        emit AssetUnlocked(asset, rsETHBurned, assetAmountUnlocked, params.rsETHPrice, params.assetPrice);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L596-603)
```text
    /// @notice Calculates the amount of asset available for withdrawal.
    /// @param asset The asset address.
    /// @return availableAssetAmount The asset amount avaialble for withdrawal.
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L700-715)
```text
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L786-815)
```text
        uint256 nextLockedNonce_ = nextLockedNonce[asset];
        // Revert when trying to unlock a request that has already been unlocked
        if (nextLockedNonce_ >= firstExcludedIndex) revert NoPendingWithdrawals();

        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```
