### Title
Operator-Gated `unlockQueue` Can Permanently Freeze User rsETH in Withdrawal Queue - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager.unlockQueue` is the sole mechanism to advance `nextLockedNonce` and allow queued withdrawals to be completed. It is restricted to `onlyAssetTransferOrOperatorRole` with no permissionless time-based fallback. A user who calls `initiateWithdrawal` transfers their rsETH irrevocably into the contract; if the privileged operator never calls `unlockQueue`, those funds are permanently frozen.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is a two-step process:

1. **User calls `initiateWithdrawal`** — rsETH is pulled from the user into the contract and a `WithdrawalRequest` is stored. The request nonce is appended to `userAssociatedNonces[asset][user]`. [1](#0-0) 

2. **User calls `completeWithdrawal`** — internally calls `_processWithdrawalCompletion`, which enforces:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
``` [2](#0-1) 

`nextLockedNonce[asset]` is **only ever incremented** inside `_unlockWithdrawalRequests`, which is called exclusively from `unlockQueue`: [3](#0-2) 

`unlockQueue` is gated by `onlyAssetTransferOrOperatorRole`: [4](#0-3) 

There is no timeout, no permissionless override, and no mechanism for a user to self-unlock their request after a waiting period. The user's rsETH sits in the contract with no exit path unless a privileged operator acts.

---

### Impact Explanation

**Temporary (escalating to permanent) freezing of user funds.**

Every rsETH deposited via `initiateWithdrawal` is held in the `LRTWithdrawalManager` contract. Without `unlockQueue` being called, `nextLockedNonce` never advances, `WithdrawalLocked` is always reverted, and `completeWithdrawal` is permanently blocked. The user has no alternative path to recover their rsETH — there is no cancel function, no timeout-based self-rescue, and no permissionless fallback. This satisfies the **Medium: Temporary freezing of funds** impact (and escalates to **Critical: Permanent freezing** if the operator role is permanently unavailable).

---

### Likelihood Explanation

The operator role is a single privileged address (or small set). Scenarios that make this realistic:

- Operator key compromise, loss, or rotation failure.
- Protocol pause combined with operator inaction (the `whenNotPaused` modifier on `unlockQueue` means a paused contract also blocks unlocking).
- Deliberate griefing by a malicious operator to selectively freeze specific users' withdrawals by never including their nonce range in a `unlockQueue` call.
- Operational outage or protocol abandonment.

Users have no on-chain recourse in any of these cases.

---

### Recommendation

Add a permissionless fallback path: allow any user to call `unlockQueue` (or a dedicated `selfUnlock`) for their own withdrawal request once a sufficient time window (e.g., `withdrawalDelayBlocks` + a grace period) has elapsed since `withdrawalStartBlock`. This mirrors the recommended fix in the reference report — allowing users to resolve their own position after enough time has passed — and eliminates the single point of failure on the operator role.

---

### Proof of Concept

1. Alice holds 10 rsETH and calls `initiateWithdrawal(stETH, 10e18, "")`.
   - `LRTWithdrawalManager` pulls 10 rsETH from Alice. [5](#0-4) 
   - Alice's request is stored at nonce `N`; `nextUnusedNonce[stETH]` becomes `N+1`. [6](#0-5) 
   - `nextLockedNonce[stETH]` remains `N` (unchanged).

2. The operator never calls `unlockQueue` (key lost, griefing, or protocol paused).

3. Alice calls `completeWithdrawal(stETH, "")` after the delay passes.
   - `_processWithdrawalCompletion` pops nonce `N` from her queue.
   - Check: `N >= nextLockedNonce[stETH]` → `N >= N` → **true** → `revert WithdrawalLocked()`. [7](#0-6) 

4. Alice's 10 rsETH is permanently locked. She has no other on-chain exit. [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
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

**File:** contracts/LRTWithdrawalManager.sol (L756-757)
```text
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

**File:** contracts/LRTWithdrawalManager.sol (L811-815)
```text
            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```
