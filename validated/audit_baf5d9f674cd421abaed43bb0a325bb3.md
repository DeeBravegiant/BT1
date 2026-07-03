### Title
Asymmetric Pause Protection Between `initiateWithdrawal` (rsETH `_transfer`) and `unlockQueue` (`burnFrom`) Causes Temporary Freezing of User rsETH — (`File: contracts/LRTWithdrawalManager.sol`)

---

### Summary

`RSETH._transfer` does **not** enforce `whenNotPaused`, while `RSETH.burnFrom` **does**. Because `initiateWithdrawal` moves rsETH via `safeTransferFrom` (which routes through `_transfer`) and `unlockQueue` destroys it via `burnFrom`, a user can successfully deposit rsETH into `LRTWithdrawalManager` while RSETH is paused, yet the operator-side unlock step is permanently blocked for the duration of the pause. No cancel/refund path exists, so the deposited rsETH is frozen until RSETH is unpaused.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is a multi-step process:

1. **`initiateWithdrawal`** — user sends rsETH to the contract; `assetsCommitted[asset]` is increased.
2. **`unlockQueue`** (operator) — burns the held rsETH and marks requests as claimable.
3. **`completeWithdrawal`** — user receives the underlying asset.

**Step 1** uses:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

This calls `RSETH._transfer`, which is overridden as:

```solidity
function _transfer(address from, address to, uint256 amount) internal override {
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);
}
```

No `whenNotPaused` guard. The transfer succeeds even when RSETH is paused. [1](#0-0) 

**Step 2** uses:

```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

`burnFrom` is declared as:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
```

It **reverts** when RSETH is paused. [2](#0-1) [3](#0-2) 

`LRTWithdrawalManager` and `RSETH` are independently pausable contracts. `LRTWithdrawalManager.initiateWithdrawal` only checks `whenNotPaused` on itself — it does **not** check whether RSETH is paused. [4](#0-3) 

There is no `cancelWithdrawal` or refund function anywhere in `LRTWithdrawalManager`. The only exit path for a user's rsETH is through `unlockQueue` → `completeWithdrawal`. [5](#0-4) 

---

### Impact Explanation

**Medium — Temporary freezing of user rsETH.**

While RSETH is paused (a legitimate security action by `PAUSER_ROLE`):

- Users can still call `initiateWithdrawal` and deposit rsETH into `LRTWithdrawalManager`.
- `unlockQueue` reverts on `burnFrom`, so no requests can be unlocked.
- `completeWithdrawal` reverts because `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]`.
- The deposited rsETH is irrecoverable until RSETH is unpaused.

All rsETH deposited during (or before) a RSETH pause is frozen with zero recourse for the user.

---

### Likelihood Explanation

**Medium.** RSETH has a dedicated `PAUSER_ROLE` that can pause it independently of `LRTWithdrawalManager`. [6](#0-5) 

Pausing RSETH is a routine emergency-response action (e.g., oracle manipulation, bridge exploit). During any such pause, the asymmetry is immediately exploitable: users who call `initiateWithdrawal` (or who already have pending requests) are stuck. The two contracts have no coordination mechanism to prevent this.

---

### Recommendation

1. **Add a `cancelWithdrawal` function** that allows users to reclaim their rsETH for requests that have not yet been unlocked (`nonce >= nextLockedNonce[asset]`). This is the minimal fix and mirrors the "borrow back" recovery path recommended in the original report.

2. **Alternatively**, check RSETH's pause state inside `initiateWithdrawal` and revert if RSETH is paused, preventing users from entering a state they cannot exit.

3. **Alternatively**, inherit `ERC20PausableUpgradeable` in RSETH so that `_transfer` also enforces `whenNotPaused`, making the two operations symmetric.

---

### Proof of Concept

```
1. RSETH is paused by PAUSER_ROLE (legitimate security action).

2. LRTWithdrawalManager is NOT paused.

3. User calls initiateWithdrawal(asset, rsETHAmount, ""):
   - whenNotPaused on LRTWithdrawalManager → passes (not paused).
   - safeTransferFrom(user, withdrawalManager, rsETHAmount):
       → RSETH._transfer() called → no whenNotPaused → SUCCEEDS.
   - assetsCommitted[asset] += expectedAssetAmount.
   - WithdrawalRequest stored at nonce N.
   - User's rsETH is now held by LRTWithdrawalManager.

4. Operator calls unlockQueue(asset, ...):
   - _unlockWithdrawalRequests() updates state.
   - IRSETH(rsETH).burnFrom(address(this), rsETHBurned):
       → whenNotPaused on RSETH → REVERTS ("Pausable: paused").
   - Entire transaction reverts; no requests are unlocked.

5. User calls completeWithdrawal(asset, ""):
   - usersFirstWithdrawalRequestNonce (= N) >= nextLockedNonce[asset] (= N)
   → revert WithdrawalLocked().

6. No cancelWithdrawal exists.

Result: User's rsETH is frozen in LRTWithdrawalManager for the entire
        duration of the RSETH pause, with no recovery path.
``` [7](#0-6) [8](#0-7) [2](#0-1) [1](#0-0)

### Citations

**File:** contracts/RSETH.sol (L183-191)
```text
    /// @dev Triggers stopped state. Contract must not be paused.
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }

    /// @dev Returns to normal state. Contract must be paused.
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```

**File:** contracts/RSETH.sol (L245-248)
```text
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/RSETH.sol (L287-291)
```text
    function _transfer(address from, address to, uint256 amount) internal override {
        _enforceNotBlocked(from);
        _enforceNotBlocked(to);
        super._transfer(from, to, amount);
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L699-717)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
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

        unlockedWithdrawalsCount[asset]--;
```
