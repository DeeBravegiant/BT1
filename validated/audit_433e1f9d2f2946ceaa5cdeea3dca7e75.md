### Title
Users Cannot Cancel Withdrawal Requests, Enabling FIFO Queue Blocking and Temporary Fund Freeze - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary
`LRTWithdrawalManager` provides no mechanism for users to cancel pending withdrawal requests. Combined with a strict FIFO unlock queue that halts entirely when it encounters a request it cannot satisfy, a large withdrawal request at the front of the queue permanently blocks all subsequent users' withdrawals until sufficient assets are available — temporarily freezing their funds.

---

### Finding Description

When a user calls `initiateWithdrawal()`, their rsETH is immediately transferred to the contract and a `WithdrawalRequest` is stored with `expectedAssetAmount` calculated at the current oracle price: [1](#0-0) 

There is no `cancelWithdrawal()` function anywhere in the contract or its interface: [2](#0-1) 

The operator-only `unlockQueue()` processes requests via `_unlockWithdrawalRequests()`, which iterates the global FIFO nonce queue and **breaks** (not continues) the moment it encounters a request whose `payoutAmount` exceeds available assets: [3](#0-2) 

Because `nextLockedNonce[asset]` advances strictly sequentially, there is no way to skip a blocking request. Any request at nonce N that cannot be satisfied prevents nonces N+1, N+2, … from ever being unlocked in the same call, regardless of how small those subsequent requests are.

When `completeWithdrawal()` is called, it pops the front of the per-user nonce queue and checks `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]`, reverting with `WithdrawalLocked` if the global queue has not advanced past that nonce: [4](#0-3) 

Users behind a blocking request have no recourse: they cannot cancel their request to reclaim rsETH, they cannot reorder their position in the queue, and they cannot bypass the global `nextLockedNonce` gate.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** Any user who submits a withdrawal request for an amount that exceeds the assets currently available in the unstaking vault will stall the entire asset queue. All users whose requests were submitted after the blocking request — regardless of how small their individual requests are — cannot complete their withdrawals until the vault accumulates sufficient assets to satisfy the blocking request first. Their rsETH remains locked in the contract with no exit path.

---

### Likelihood Explanation

**Medium.** This requires no special privileges. Any rsETH holder can call `initiateWithdrawal()` with a large amount (up to `getAvailableAssetAmount(asset)` at request time). If the vault's asset balance subsequently drops (e.g., due to EigenLayer slashing, other withdrawals draining the vault, or a large committed amount reducing availability), the blocking condition is met. The FIFO break is deterministic and requires no operator action to trigger.

---

### Recommendation

1. **Add a `cancelWithdrawal()` function** that allows a user to remove their own pending (not yet unlocked) withdrawal request, decrement `assetsCommitted[asset]` by the original `expectedAssetAmount`, and return the locked rsETH to the user.
2. **Consider a skip-ahead mechanism** in `_unlockWithdrawalRequests` that uses `continue` instead of `break` when a single request cannot be satisfied, so that smaller subsequent requests are not blocked by one large request.

---

### Proof of Concept

1. Alice calls `initiateWithdrawal(stETH, largeAmount, "")`. The vault currently holds `X` stETH. `largeAmount` maps to `expectedAssetAmount = X - 1` (just under the limit at request time). `assetsCommitted[stETH] += X - 1`.
2. Bob calls `initiateWithdrawal(stETH, smallAmount, "")` for 1 wei of rsETH. His nonce is Alice's nonce + 1.
3. The vault balance drops to `X - 2` (e.g., another withdrawal or slashing event).
4. Operator calls `unlockQueue(stETH, ...)`. Inside `_unlockWithdrawalRequests`, Alice's request requires `payoutAmount ≈ X - 1` but `availableAssetAmount = X - 2`. The loop **breaks** at Alice's nonce.
5. `nextLockedNonce[stETH]` does not advance. Bob's nonce remains `>= nextLockedNonce[stETH]`.
6. Bob calls `completeWithdrawal(stETH, "")`. `_processWithdrawalCompletion` pops Bob's nonce and checks `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]` → **reverts with `WithdrawalLocked`**.
7. Bob has no `cancelWithdrawal()` to call. His rsETH is frozen indefinitely until the vault accumulates enough assets to satisfy Alice's request first. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTWithdrawalManager.sol (L790-815)
```text
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

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L80-110)
```text
    // methods
    function getExpectedAssetAmount(address asset, uint256 amount) external view returns (uint256);

    function getAvailableAssetAmount(address asset) external view returns (uint256 assetAmount);

    function getUserWithdrawalRequest(
        address asset,
        address user,
        uint256 index
    )
        external
        view
        returns (uint256 rsETHAmount, uint256 expectedAssetAmount, uint256 withdrawalStartBlock, uint256 userNonce);

    function initiateWithdrawal(address asset, uint256 withdrawAmount, string calldata referralId) external;

    function completeWithdrawal(address asset, string calldata referralId) external;

    function completeWithdrawalForUser(address asset, address user, string calldata referralId) external;

    function unlockQueue(
        address asset,
        uint256 index,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked);

```
