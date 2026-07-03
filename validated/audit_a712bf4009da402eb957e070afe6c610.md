### Title
`whenNotPaused` on `completeWithdrawal` Allows Privileged Role to Permanently Freeze User rsETH Already Held in Contract - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` applies `whenNotPaused` to `completeWithdrawal`, `completeWithdrawalForUser`, and `instantWithdrawal`. Because `initiateWithdrawal` transfers rsETH from the user into the contract before any unlock occurs, a pause applied after initiation leaves that rsETH irrecoverable until the contract is unpaused. If the admin renounces ownership or is compromised, the funds are permanently frozen.

### Finding Description
When a user calls `initiateWithdrawal`, their rsETH is immediately pulled into the contract: [1](#0-0) 

The only way to recover those funds is through `completeWithdrawal` (or `instantWithdrawal`), both of which carry `whenNotPaused`: [2](#0-1) [3](#0-2) 

The `PAUSER_ROLE` can pause the contract unilaterally: [4](#0-3) 

Unpausing requires `onlyLRTAdmin`: [5](#0-4) 

If the admin renounces the admin role (or is compromised), no one can unpause, and all rsETH already deposited into the contract via `initiateWithdrawal` is permanently frozen. There is no emergency withdrawal path for users.

### Impact Explanation
**High.** User rsETH principal is transferred into the contract at `initiateWithdrawal` time. If the contract is paused and cannot be unpaused (e.g., admin renounces role), those funds are permanently frozen. This matches the "Permanent freezing of funds" (Critical) or at minimum "Temporary freezing of funds" (Medium) impact categories.

### Likelihood Explanation
**Low.** Requires either a malicious `PAUSER_ROLE` combined with an unresponsive or renounced admin, or a compromised admin key. This is the same likelihood profile as the reference report.

### Recommendation
Remove `whenNotPaused` from `completeWithdrawal` and `instantWithdrawal` so that users can always recover funds they have already deposited into the contract. Pausing should only block new deposits (`initiateWithdrawal`), not exits. Alternatively, add an emergency user-withdrawal path that bypasses the pause check.

### Proof of Concept
1. User calls `initiateWithdrawal(asset, rsETHAmount, referralId)` — rsETH is transferred to `LRTWithdrawalManager` at line 166.
2. Operator with `PAUSER_ROLE` calls `pause()` (line 347–349).
3. Admin renounces the LRT admin role (or is compromised).
4. User calls `completeWithdrawal(asset, referralId)` — reverts at the `whenNotPaused` check (line 183).
5. `instantWithdrawal` also reverts at `whenNotPaused` (line 219).
6. No unpause is possible; rsETH held in the contract is permanently frozen. [6](#0-5) [2](#0-1) [7](#0-6)

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

**File:** contracts/LRTWithdrawalManager.sol (L212-222)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
```

**File:** contracts/LRTWithdrawalManager.sol (L347-354)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }

    /// @dev Returns to normal state. Contract must be paused.
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```
