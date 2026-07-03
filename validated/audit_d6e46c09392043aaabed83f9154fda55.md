### Title
`completeWithdrawal` Blocked by Pause Freezes In-Flight User rsETH - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` gates `completeWithdrawal` (and `unlockQueue`) behind `whenNotPaused`, but `initiateWithdrawal` already transfers the user's rsETH into the contract before the queue is processed. If the contract is paused after a user has deposited rsETH but before they can complete the withdrawal, their rsETH is frozen with no cancel or recovery path available to them.

### Finding Description
The withdrawal lifecycle in `LRTWithdrawalManager` is a two-step process:

**Step 1 — `initiateWithdrawal`:** The user's rsETH is pulled into the contract immediately.

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**Step 2 — `completeWithdrawal`:** The user calls this to receive their LST/ETH. It is gated by `whenNotPaused`:

```solidity
function completeWithdrawal(address asset, string calldata referralId)
    external nonReentrant whenNotPaused { ... }
```

`unlockQueue` (the operator step between initiation and completion) is also gated by `whenNotPaused`:

```solidity
function unlockQueue(...) external nonReentrant onlySupportedAsset(asset) whenNotPaused onlyAssetTransferOrOperatorRole ...
```

There is no `cancelWithdrawal` function. Once a user's rsETH is in the contract, the only exit paths are `completeWithdrawal` and `completeWithdrawalForUser` — both blocked by `whenNotPaused`. The user has no way to recover their rsETH while the contract is paused.

This is structurally identical to the reference vulnerability: a user performs their side of the transaction (repays loan / transfers rsETH), but a contract-level flag (`live == 0` / `paused == true`) prevents them from receiving what they are owed.

### Impact Explanation
**Medium. Temporary freezing of funds.** Any user who has called `initiateWithdrawal` and had their rsETH transferred into `LRTWithdrawalManager` cannot complete the withdrawal or recover their rsETH for the entire duration of the pause. The freeze lasts until `onlyLRTAdmin` calls `unpause()`. If the admin is slow to respond or the pause is extended, users are locked out of their funds for an indeterminate period.

### Likelihood Explanation
**Low.** The `PAUSER_ROLE` is a distinct, lower-threshold role from `LRTAdmin`. It is designed to be used reactively in security incidents. A realistic scenario: a suspected exploit triggers an emergency pause; the PAUSER_ROLE holder pauses the contract without realising that users with in-flight withdrawal requests have already surrendered their rsETH and now have no recourse until the admin manually unpauses.

### Recommendation
Remove `whenNotPaused` from `completeWithdrawal` (and optionally `completeWithdrawalForUser`). Users who have already transferred rsETH into the contract should always be able to claim the assets they are owed. Alternatively, add a `cancelWithdrawal` function that returns the user's rsETH when the contract is paused, mirroring the fix applied to the reference `CollateralJoin` vulnerability.

### Proof of Concept
1. User calls `initiateWithdrawal(stETH, 1e18, "")` → 1 rsETH transferred from user to `LRTWithdrawalManager`.
2. `PAUSER_ROLE` calls `LRTWithdrawalManager.pause()`.
3. Operator attempts `unlockQueue(stETH, ...)` → reverts: `"Pausable: paused"`. Request remains locked.
4. User calls `completeWithdrawal(stETH, "")` → reverts: `"Pausable: paused"`.
5. User's 1 rsETH remains in `LRTWithdrawalManager` with no recovery path until admin calls `unpause()`.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
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

**File:** contracts/LRTWithdrawalManager.sol (L347-349)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }
```
