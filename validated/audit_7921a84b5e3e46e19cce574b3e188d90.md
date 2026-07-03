### Title
Withdrawal Queue Head-of-Line Blocking Temporarily Freezes Subsequent User Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`_unlockWithdrawalRequests` in `LRTWithdrawalManager` processes the withdrawal queue in strict FIFO order and unconditionally `break`s when the available asset amount is insufficient to cover the head-of-queue request. Any withdrawal requests behind a large unfulfillable request are permanently stalled until the blocking request is serviced, even if there is sufficient liquidity to cover all subsequent smaller requests.

### Finding Description
`_unlockWithdrawalRequests` iterates over pending withdrawal requests starting from `nextLockedNonce[asset]`:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

    if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [1](#0-0) 

The `break` at line 800 exits the entire loop and persists `nextLockedNonce[asset]` at the blocking request's position. Because `nextLockedNonce` is the sole cursor that advances the queue, no subsequent request — regardless of its size — can ever be unlocked until the head request is fully serviced. There is no skip, partial-fill, or out-of-order processing mechanism.

The queue is populated by `initiateWithdrawal`, which appends each new request to the back of the per-asset nonce sequence:

```solidity
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
``` [2](#0-1) 

A user who submits a large withdrawal request (e.g., a whale committing the majority of `getAvailableAssetAmount`) before other users submit smaller requests will occupy the head of the queue. If the `LRTUnstakingVault` does not hold enough of the asset to cover that large request at the time `unlockQueue` is called, the operator's call to `unlockQueue` will `break` immediately and leave all subsequent requests locked.

`getAvailableAssetAmount` computes availability as `totalAssetDeposits - assetsCommitted`, where `totalAssetDeposits` aggregates balances across the deposit pool, all NodeDelegators, and EigenLayer strategies: [3](#0-2) 

However, `unlockQueue` passes `unstakingVault.balanceOf(asset)` — the vault's *liquid* balance — as `availableAssetAmount`, not the full `totalAssetDeposits`. Assets still restaked in EigenLayer are not liquid and cannot satisfy the payout. This means the blocking condition is realistic whenever the vault has not yet received a completed EigenLayer unstaking for the large request.

### Impact Explanation
**Medium — Temporary freezing of funds.**

All users whose withdrawal requests are queued behind a large unfulfillable request cannot complete their withdrawals. Their rsETH has already been transferred into the `LRTWithdrawalManager` at `initiateWithdrawal` time:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
``` [4](#0-3) 

Those tokens are held by the contract and cannot be reclaimed. The affected users' funds are frozen until the blocking request is serviced. In a scenario where the blocking request is very large relative to the vault's replenishment rate (e.g., due to EigenLayer's unstaking delay of ~7 days plus the protocol's own 8-day `withdrawalDelayBlocks`), the freeze can persist for weeks.

### Likelihood Explanation
**Medium.** The scenario requires:
1. A large withdrawal request to be submitted before smaller ones — a natural occurrence given that whale users exist and there is no ordering protection.
2. The `LRTUnstakingVault` to hold less liquid asset than the large request requires at the time `unlockQueue` is called — also natural, since assets are predominantly deployed to EigenLayer and must be explicitly unstaked first.

No privileged access, oracle manipulation, or external protocol compromise is required. Any unprivileged rsETH holder can trigger this by submitting a large `initiateWithdrawal` before other users.

### Recommendation
Replace the `break` with a `continue` so that the queue processor skips requests it cannot currently fulfill and services smaller subsequent requests:

```solidity
if (availableAssetAmount < payoutAmount) continue; // Skip, try next request
```

Alternatively, implement partial fulfillment: allow a request to be split so that the available liquidity is disbursed immediately and the remainder stays locked. Additionally, consider allowing `initiateWithdrawal` to succeed even when vault liquidity is insufficient (relying solely on `assetsCommitted` accounting), consistent with the recommendation in the reference report.

### Proof of Concept
1. Protocol has 100 ETH liquid in `LRTUnstakingVault`; 900 ETH is still restaked in EigenLayer.
2. Alice calls `initiateWithdrawal(ETH, rsETH_for_500_ETH)`. Her request gets nonce 0. `assetsCommitted[ETH] += 500`.
3. Bob calls `initiateWithdrawal(ETH, rsETH_for_1_ETH)`. His request gets nonce 1. `assetsCommitted[ETH] += 1`.
4. Operator calls `unlockQueue(ETH, 2, ...)`. Inside `_unlockWithdrawalRequests`:
   - `availableAssetAmount = 100` (vault liquid balance).
   - Nonce 0: `payoutAmount = 500`. `100 < 500` → **`break`**. Loop exits.
   - Nonce 1 (Bob's 1 ETH request) is **never reached**.
5. `nextLockedNonce[ETH]` remains at 0. Bob cannot call `completeWithdrawal` because his nonce (1) is `>= nextLockedNonce[ETH]` (0), triggering `WithdrawalLocked`. [5](#0-4) [6](#0-5) 

Bob's 1 ETH worth of rsETH remains locked in the contract with no recourse until Alice's 500 ETH request is fully serviced.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L706-707)
```text
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L755-757)
```text
        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
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
