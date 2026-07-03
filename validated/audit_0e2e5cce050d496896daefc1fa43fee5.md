### Title
`completeWithdrawal` blocked by `whenNotPaused`, trapping user rsETH already held in contract - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.completeWithdrawal` carries the `whenNotPaused` modifier. Because `initiateWithdrawal` transfers the user's rsETH into the contract before the withdrawal is finalised, any pause issued after initiation leaves that rsETH permanently locked until the contract is unpaused — with no escape hatch for the user.

### Finding Description
The two-step withdrawal flow works as follows:

1. **`initiateWithdrawal`** — the user's rsETH is pulled into `LRTWithdrawalManager` via `safeTransferFrom`, and a `WithdrawalRequest` is recorded.
2. **`completeWithdrawal`** — after the delay and an operator `unlockQueue` call, the user calls this to receive their LST/ETH.

Both steps are gated by `whenNotPaused`:

```solidity
// step 1 – line 158
function initiateWithdrawal(...) external nonReentrant whenNotPaused ...
    IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
    ...

// step 2 – line 183
function completeWithdrawal(address asset, string calldata referralId)
    external nonReentrant whenNotPaused { ... }
```

If the contract is paused between steps 1 and 2, the user's rsETH is held by the contract with no way to retrieve it. There is no emergency-withdrawal path, no cancel-and-refund function, and no version of `completeWithdrawal` that bypasses the pause. The operator-assisted `completeWithdrawalForUser` (line 192) is equally blocked by `whenNotPaused`.

### Impact Explanation
Any user who has already called `initiateWithdrawal` (and therefore transferred rsETH to the contract) cannot recover their funds for the entire duration of the pause. The rsETH is held by the contract, the corresponding LST/ETH is committed in `assetsCommitted`, and neither asset is accessible. This constitutes a **temporary freeze of user funds**.

### Likelihood Explanation
The `PAUSER_ROLE` is a separate, lower-privilege role that can pause the contract without admin rights. Pauses are expected to occur during security incidents or upgrades. The window between `initiateWithdrawal` and `completeWithdrawal` is at least `withdrawalDelayBlocks` (default ~8 days), giving a wide exposure window during which a pause can trap funds.

### Recommendation
Remove `whenNotPaused` from `completeWithdrawal` and `completeWithdrawalForUser`. Pausing should block new deposits and new withdrawal initiations, but must never block the completion of withdrawals for which rsETH has already been surrendered to the contract. Optionally, add a `cancelWithdrawal` function that refunds the rsETH to the user even while paused, as an additional safety valve.

### Proof of Concept
1. Alice calls `initiateWithdrawal(stETH, 1e18, "")`. The contract pulls `1e18` rsETH from Alice and records her request. [1](#0-0) 
2. The `PAUSER_ROLE` calls `pause()`. [2](#0-1) 
3. After the delay passes and the operator unlocks the queue, Alice calls `completeWithdrawal(stETH, "")`. The call reverts because of `whenNotPaused`. [3](#0-2) 
4. `completeWithdrawalForUser` is equally blocked. [4](#0-3) 
5. Alice's `1e18` rsETH remains locked in the contract for the entire duration of the pause, with no recovery path available. [5](#0-4)

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

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L192-204)
```text
    function completeWithdrawalForUser(
        address asset,
        address user,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        _processWithdrawalCompletion(asset, user, referralId);
        emit AssetWithdrawalCompletedBy(msg.sender);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L347-349)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }
```
