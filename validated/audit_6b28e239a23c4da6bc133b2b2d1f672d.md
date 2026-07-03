### Title
FIFO Withdrawal Queue Blocked by Large Front-of-Queue Request — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager._unlockWithdrawalRequests` processes the withdrawal queue in strict FIFO order and unconditionally `break`s when the vault's current balance cannot cover the front request's payout. Because `initiateWithdrawal` gates admission using `getTotalAssetDeposits` (which includes EigenLayer-staked assets) while `unlockQueue` supplies only `unstakingVault.balanceOf` as available liquidity, a malicious depositor can place a large request at the head of the queue that the vault cannot immediately satisfy, permanently stalling every subsequent user's withdrawal until the vault accumulates enough assets to cover the blocker.

---

### Finding Description

**`initiateWithdrawal` admission check (line 170):**

```solidity
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` computes capacity against `getTotalAssetDeposits`, which aggregates assets across the deposit pool, all NodeDelegators, EigenLayer strategies, the converter, and the unstaking vault:

```solidity
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
``` [1](#0-0) 

`getTotalAssetDeposits` sums all protocol locations including EigenLayer:

```solidity
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
        + assetLyingUnstakingVault);
``` [2](#0-1) 

**`unlockQueue` supplies only the vault balance (line 286/849):**

```solidity
return UnlockParams({
    rsETHPrice: lrtOracle.rsETHPrice(),
    assetPrice: lrtOracle.getAssetPrice(asset),
    totalAvailableAssets: unstakingVault.balanceOf(asset)   // ← vault only
});
``` [3](#0-2) 

**`_unlockWithdrawalRequests` breaks on the first under-funded request (line 800):**

```solidity
if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
``` [4](#0-3) 

Because `nextLockedNonce[asset]` advances only when a request is successfully unlocked, any request that cannot be covered freezes every request behind it indefinitely. [5](#0-4) 

---

### Impact Explanation

**Temporary freezing of funds (Medium).**

All users whose withdrawal requests sit behind the blocker in the FIFO queue cannot call `completeWithdrawal` (their nonce is still `>= nextLockedNonce[asset]`, so `_processWithdrawalCompletion` reverts with `WithdrawalLocked`). Their rsETH has already been transferred into the contract at `initiateWithdrawal` time, so they cannot reclaim it either. The freeze persists until the vault accumulates enough assets to cover the blocker's `expectedAssetAmount`, which may require multiple EigenLayer unstaking cycles (days to weeks per cycle). [6](#0-5) 

---

### Likelihood Explanation

Any rsETH holder can call `initiateWithdrawal`. The attacker needs only to be first in the queue for a given asset (or front-run other users' `initiateWithdrawal` transactions in the mempool). The gap between `getTotalAssetDeposits` and `unstakingVault.balanceOf` is structural and large in normal operation: the vast majority of protocol assets reside in EigenLayer strategies, not in the vault. The attacker does not lose funds — their rsETH is locked in the queue and will eventually be claimable — so the cost of the attack is only opportunity cost and gas. [7](#0-6) 

---

### Recommendation

Replace the hard `break` with a `continue` (skip the under-funded request and attempt to unlock subsequent smaller requests), or implement a skip-and-requeue mechanism so that a single large request does not block the entire queue. Alternatively, cap `expectedAssetAmount` at admission time against `unstakingVault.balanceOf` rather than `getTotalAssetDeposits`, so that only requests the vault can immediately service are admitted to the queue.

---

### Proof of Concept

**Setup (representative numbers):**
- Protocol holds 1 000 ETH total: 950 ETH in EigenLayer, 50 ETH in `LRTUnstakingVault`.
- `assetsCommitted[ETH]` = 0.

**Step 1 — Attacker calls `initiateWithdrawal`:**
- Burns rsETH worth 500 ETH.
- `getAvailableAssetAmount` returns 1 000 ETH → check passes.
- `assetsCommitted[ETH]` becomes 500 ETH.
- Attacker's request is enqueued at `nextUnusedNonce = 0`.

**Step 2 — Victims call `initiateWithdrawal`:**
- Each burns rsETH worth 10 ETH.
- `getAvailableAssetAmount` = 1 000 − 500 = 500 ETH → checks pass.
- Victims' requests are enqueued at nonces 1, 2, 3, …

**Step 3 — Operator calls `unlockQueue`:**
- `availableAssetAmount` = `unstakingVault.balanceOf(ETH)` = 50 ETH.
- Loop iteration 0: attacker's `payoutAmount` = 500 ETH. `50 < 500` → **`break`**.
- Victims' requests at nonces 1, 2, 3, … are never reached.
- `nextLockedNonce[ETH]` stays at 0.

**Step 4 — Victims attempt `completeWithdrawal`:**
- Their nonces (1, 2, 3, …) are all `>= nextLockedNonce[ETH]` (= 0 after the break, which never advanced past 0).

Wait — actually `nextLockedNonce` stays at 0 because the attacker's request at nonce 0 was never unlocked. Victims' nonces 1, 2, 3 are all `>= 0` (= `nextLockedNonce`), so `_processWithdrawalCompletion` reverts with `WithdrawalLocked` for all of them. [8](#0-7) 

The vault must accumulate 500 ETH (requiring multiple EigenLayer withdrawal cycles) before the attacker's request can be unlocked and the queue can advance. The attacker can repeat the attack each time the queue clears.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-175)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L846-851)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```

**File:** contracts/LRTDepositPool.sol (L394-397)
```text
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```
