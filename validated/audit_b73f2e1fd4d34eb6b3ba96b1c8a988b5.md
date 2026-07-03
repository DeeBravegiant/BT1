### Title
`_unlockWithdrawalRequests` Exits Loop Early on Insufficient Liquidity, Freezing Later Requests - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager::_unlockWithdrawalRequests` uses a `break` when the available asset amount is insufficient to cover a single withdrawal request's payout. This exits the entire loop, permanently blocking all subsequent requests in the queue from being unlocked — even those with smaller payout amounts that could be fully covered by the available liquidity.

---

### Finding Description

`_unlockWithdrawalRequests` iterates through queued withdrawal requests in FIFO order, unlocking them against `availableAssetAmount`: [1](#0-0) 

At line 800, when `availableAssetAmount < payoutAmount` for request at nonce N, the loop `break`s: [2](#0-1) 

Because `nextLockedNonce_` is only incremented on successful processing (line 812), the state variable `nextLockedNonce[asset]` is written back as N — the blocked request's position: [3](#0-2) 

On every subsequent call to `_unlockWithdrawalRequests`, the loop restarts at nonce N and hits the same `break` again, permanently blocking all requests at nonces N+1, N+2, … regardless of their individual `payoutAmount` values.

The `payoutAmount` for each request is computed as `min(expectedAssetAmount, rsETHUnstaked * rsETHPrice / assetPrice)`: [4](#0-3) 

Different users unstake different amounts of rsETH, so request N can have a large `payoutAmount` while requests N+1, N+2, … have small ones that are fully coverable by available liquidity. The `break` prevents those smaller requests from ever being reached.

---

### Impact Explanation

Users whose withdrawal requests are queued behind a single large request are unable to withdraw their assets until the protocol accumulates enough liquidity to cover the large request first. This constitutes **temporary freezing of funds** for an unbounded set of users.

**Impact: Medium — Temporary freezing of funds.**

---

### Likelihood Explanation

Any rsETH holder can queue a withdrawal request via the public withdrawal initiation path. A user who queues a large withdrawal early (obtaining a low nonce) will block all later, smaller requests whenever available liquidity is below their `payoutAmount`. This requires no privilege and is trivially achievable by any rsETH holder with a meaningful balance. The condition is realistic whenever the protocol's liquid ETH/LST balance is partially depleted (e.g., during high withdrawal demand or after a large EigenLayer unstaking cycle).

---

### Recommendation

The loop must not stop at the first request it cannot cover. Because the while loop only advances `nextLockedNonce_` on success, a simple `continue` would create an infinite loop. The correct fix is to restructure the loop to always advance the nonce counter and separately track which requests were actually unlocked:

```diff
while (nextLockedNonce_ < firstExcludedIndex) {
    bytes32 requestId = getRequestId(asset, nextLockedNonce_);
    WithdrawalRequest storage request = withdrawalRequests[requestId];

    if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

-   if (availableAssetAmount < payoutAmount) break;
+   if (availableAssetAmount < payoutAmount) {
+       unchecked { nextLockedNonce_++; }
+       continue; // skip this request, check the next one
+   }

    assetsCommitted[asset] -= request.expectedAssetAmount;
    request.expectedAssetAmount = payoutAmount;
    rsETHAmountToBurn += request.rsETHUnstaked;
    availableAssetAmount -= payoutAmount;
    assetAmountToUnlock += payoutAmount;
    unlockedWithdrawalsCount[asset]++;

    unchecked { nextLockedNonce_++; }
}
```

Note: advancing the nonce past a skipped request means it will never be revisited. A more robust solution would maintain a separate "pending" set for skipped requests, or process them in a separate pass. The exact design depends on the protocol's fairness requirements.

---

### Proof of Concept

**Setup:**
- `withdrawalDelayBlocks` has passed for all requests.
- `availableAssetAmount = 10 ETH`
- Request at nonce 5: `payoutAmount = 50 ETH` (large rsETH unstaker)
- Request at nonce 6: `payoutAmount = 1 ETH` (small rsETH unstaker)
- `nextLockedNonce[ETH] = 5`, `firstExcludedIndex = 7`

**Execution of `_unlockWithdrawalRequests`:**

1. Loop iteration: `nextLockedNonce_ = 5`. Delay check passes.
2. `payoutAmount = 50 ETH`. `availableAssetAmount (10) < payoutAmount (50)` → **`break`**.
3. `nextLockedNonce[ETH]` is written back as `5`.

**Result:** Request at nonce 6 (1 ETH, fully coverable) is never reached. The user at nonce 6 cannot withdraw. Every future call to `_unlockWithdrawalRequests` restarts at nonce 5 and hits the same `break`, permanently blocking nonce 6 until 50 ETH of liquidity is available. [5](#0-4)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
