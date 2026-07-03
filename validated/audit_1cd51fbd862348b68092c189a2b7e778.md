### Title
Push-Pattern ETH Transfer in `_processWithdrawalCompletion` Permanently Freezes Funds for Non-Payable Contract Withdrawers - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` uses a push pattern to deliver ETH to withdrawers in `_processWithdrawalCompletion`. If a user's address is a smart contract without a `receive()` function (or one that reverts on ETH receipt), every call to `completeWithdrawal` permanently reverts. Because rsETH is already burned during the prior `unlockQueue` step, the user's funds are irretrievably frozen with no recovery path.

### Finding Description
The withdrawal lifecycle in `LRTWithdrawalManager` is a three-step process:

1. **`initiateWithdrawal`** — user transfers rsETH to the contract and a withdrawal request is queued.
2. **`unlockQueue`** (operator-only) — rsETH is burned via `IRSETH.burnFrom`, and the corresponding asset amount is pulled from `LRTUnstakingVault` into `LRTWithdrawalManager`.
3. **`completeWithdrawal`** — assets are pushed to the user.

The critical path is in `_processWithdrawalCompletion`:

```solidity
// line 712: request record deleted
delete withdrawalRequests[requestId];
// line 717: count decremented
unlockedWithdrawalsCount[asset]--;
// line 734: push transfer — reverts if `user` cannot receive ETH
_transferAsset(asset, user, request.expectedAssetAmount);
```

`_transferAsset` for ETH uses a low-level call:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

If `to` is a contract that rejects ETH (no `receive()`, or a reverting `receive()`), this call returns `sent = false` and the entire transaction reverts. Because the revert unwinds all state changes, the user can retry — but every retry will fail identically. There is no admin escape hatch to redirect the payout to a different address, and no mechanism to cancel the unlocked request and restore rsETH (which was already burned in step 2). The assets remain locked in `LRTWithdrawalManager` indefinitely.

The operator-facing `completeWithdrawalForUser` provides no relief: it calls the same `_processWithdrawalCompletion` and hits the same revert. [1](#0-0) [2](#0-1) 

### Impact Explanation
**Critical — Permanent freezing of funds.**

Once `unlockQueue` burns the user's rsETH and the request is marked unlocked, the only way to recover the underlying ETH is through `completeWithdrawal`. If that path is permanently blocked, the ETH is stranded in `LRTWithdrawalManager` with no admin recovery function. The user loses both their rsETH (burned) and their ETH (undeliverable). Additionally, `unlockedWithdrawalsCount[asset]` can never reach zero for that asset, permanently blocking `sweepRemainingAssets` for all users of that asset. [3](#0-2) [4](#0-3) 

### Likelihood Explanation
**Medium.** Smart contract wallets (Gnosis Safe, multisigs, DAO treasuries) are the primary holders of large rsETH positions and are the most likely to initiate ETH withdrawals. Many such contracts do not implement a `receive()` function or implement one that conditionally reverts. The protocol has no on-chain check at `initiateWithdrawal` time to verify that the caller can receive ETH, and no warning mechanism exists. The scenario requires no attacker — it is triggered by ordinary protocol usage from a contract address. [5](#0-4) 

### Recommendation
Replace the push pattern for ETH with a pull pattern. Instead of calling `payable(to).call{value: amount}("")` inside `_processWithdrawalCompletion`, record the owed amount in a mapping (e.g., `mapping(address => uint256) public pendingETHWithdrawals`) and emit an event. Provide a separate `claimETH()` function that the user calls to pull their ETH. This mirrors the fix described in the referenced report and eliminates the permanent-freeze risk entirely, since a failed pull only affects the caller's own transaction. [2](#0-1) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

contract NoReceive {
    // No receive() or fallback() — rejects all ETH

    function initiateAndFreeze(
        address withdrawalManager,
        address rsETH,
        uint256 rsETHAmount
    ) external {
        // Step 1: approve and initiate withdrawal
        IERC20(rsETH).approve(withdrawalManager, rsETHAmount);
        ILRTWithdrawalManager(withdrawalManager)
            .initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");

        // Step 2: operator calls unlockQueue externally — rsETH is burned

        // Step 3: completeWithdrawal always reverts with EthTransferFailed
        // because this contract has no receive()
        ILRTWithdrawalManager(withdrawalManager)
            .completeWithdrawal(ETH_TOKEN, "");
        // ^^^ ALWAYS REVERTS — funds permanently frozen
    }
}
```

1. Deploy `NoReceive` and fund it with rsETH.
2. Call `initiateAndFreeze` — the `initiateWithdrawal` succeeds.
3. Operator calls `unlockQueue` — rsETH is burned, ETH moved to `LRTWithdrawalManager`.
4. Any call to `completeWithdrawal` (or `completeWithdrawalForUser`) for this address reverts at `_transferAsset` with `EthTransferFailed`.
5. The ETH is permanently locked in `LRTWithdrawalManager`; `unlockedWithdrawalsCount[ETH_TOKEN]` never decrements for this request. [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
```

**File:** contracts/LRTWithdrawalManager.sol (L699-738)
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

        // If Aave integration is enabled and asset is ETH, withdraw from Aave if needed
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
            uint256 contractBalance = address(this).balance;
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L876-883)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
    }
```
