### Title
Malicious ETH Withdrawal Recipient Can Gas-Grief the Operator via `completeWithdrawalForUser` - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager._transferAsset` sends ETH to a user-controlled address using an unbounded `.call{value: amount}("")`. A malicious recipient contract can implement a `receive()` function that consumes all forwarded gas, causing the operator's `completeWithdrawalForUser` transaction to revert and wasting gas. This can be repeated across many small ETH withdrawal requests, constituting unbounded gas consumption against the operator.

### Finding Description
`_transferAsset` is the single ETH dispatch primitive in `LRTWithdrawalManager`:

```solidity
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
``` [1](#0-0) 

This is called from `_processWithdrawalCompletion`, which is invoked by both `completeWithdrawal` (user-initiated) and `completeWithdrawalForUser` (operator-initiated):

```solidity
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
    ...
}
``` [2](#0-1) 

The ETH transfer at line 878 forwards all remaining gas to the recipient. A malicious contract at `user` can implement a `receive()` that spins in a loop consuming all gas, causing the operator's transaction to run out of gas and revert. The code comment on `completeWithdrawalForUser` acknowledges this scenario but incorrectly dismisses it:

```
/// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
``` [3](#0-2) 

The function has no ETH guard and is fully reachable for ETH withdrawals. The `_processWithdrawalCompletion` path that calls `_transferAsset` is: [4](#0-3) 

### Impact Explanation
Every time the operator calls `completeWithdrawalForUser` for a malicious ETH withdrawal recipient, the transaction consumes all gas and reverts. An attacker can deploy many such contracts, each initiating a minimum-size ETH withdrawal (bounded only by `minRsEthAmountToWithdraw`), and force the operator to waste unbounded gas across repeated failed attempts. This matches the **Medium — Unbounded gas consumption** impact class.

Additionally, since `completeWithdrawal` (user self-service) routes through the same `_transferAsset` call, a user whose contract `receive()` always reverts will have their ETH withdrawal permanently unclaimable — there is no admin escape hatch to redirect the ETH to a different address.

### Likelihood Explanation
**Low.** The attacker must hold rsETH to initiate a withdrawal and is locking their own funds in the process. The operator is not obligated to call `completeWithdrawalForUser` and can stop doing so for known-malicious addresses. However, the attack is cheap to repeat with many small contracts and requires no special privilege beyond being an rsETH holder.

### Recommendation
1. Cap the gas forwarded to the recipient in `_transferAsset`, e.g. use a fixed gas stipend (`call{gas: 2300, value: amount}`) or wrap the ETH in WETH and transfer the ERC-20 instead.
2. Alternatively, implement a pull-payment pattern: record the owed ETH amount per user and let them claim it separately, removing the push-transfer from the operator-callable path entirely.
3. Remove or enforce the comment "Not expected to be used for ETH" by adding an explicit `require(asset != LRTConstants.ETH_TOKEN)` guard on `completeWithdrawalForUser` if ETH is truly not intended to be handled there.

### Proof of Concept
```solidity
// Malicious recipient
contract GasGriefRecipient {
    // Burns all forwarded gas
    receive() external payable {
        uint256 i;
        while (true) { unchecked { i++; } }
    }

    function attack(address withdrawalManager, address rsETH, uint256 amount) external {
        // 1. Approve and initiate ETH withdrawal
        IERC20(rsETH).approve(withdrawalManager, amount);
        ILRTWithdrawalManager(withdrawalManager).initiateWithdrawal(
            0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE, // ETH_TOKEN
            amount,
            "grief"
        );
        // 2. Wait for operator to call completeWithdrawalForUser(ETH, address(this), ...)
        //    => operator's tx runs out of gas and reverts
    }
}
```

The operator's call to `completeWithdrawalForUser(ETH_TOKEN, address(griefContract), "")` reaches `_transferAsset` at line 878, forwards all remaining gas to `griefContract.receive()`, exhausts it, and reverts — wasting the operator's full gas budget. Repeating this across N malicious contracts costs the operator O(N × block_gas_limit) in wasted gas. [1](#0-0) [5](#0-4)

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

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
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
