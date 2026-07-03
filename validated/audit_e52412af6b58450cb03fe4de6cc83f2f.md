Audit Report

## Title
ETH Withdrawal Permanently Frozen When Recipient Contract Cannot Receive Native ETH - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager._transferAsset` delivers ETH via a low-level `.call{value:}` and hard-reverts on failure. Because `unlockQueue` burns rsETH and moves ETH into the contract in a prior transaction, any subsequent `completeWithdrawal` call that fails due to a non-ETH-receivable recipient leaves the ETH permanently stuck: the `unlockedWithdrawalsCount` counter stays non-zero, blocking the only escape hatch (`sweepRemainingAssets`), with no other on-chain recovery path.

## Finding Description
The withdrawal lifecycle is split across two separate transactions:

**Step 1 — `initiateWithdrawal` (L150-178):** rsETH is pulled from `msg.sender` into `LRTWithdrawalManager`. No restriction on `msg.sender` type — any contract address may call this.

**Step 2 — `unlockQueue` (L301-307):** rsETH held by the contract is burned via `IRSETH.burnFrom`, and the corresponding ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`. After this point, the rsETH is irreversibly gone and ETH sits in the contract.

**Step 3 — `completeWithdrawal` / `completeWithdrawalForUser` → `_processWithdrawalCompletion` (L699-738):** The function executes in order:
- `userAssociatedNonces[asset][user].popFront()` (L705)
- `delete withdrawalRequests[requestId]` (L712)
- `unlockedWithdrawalsCount[asset]--` (L717)
- `_transferAsset(asset, user, request.expectedAssetAmount)` (L734)

`_transferAsset` for ETH (L876-883):
```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

If `to` is a contract with no `receive()` or one that explicitly reverts, `sent == false` and `EthTransferFailed` is thrown. The entire transaction reverts, rolling back all state mutations including the `unlockedWithdrawalsCount[asset]--`. The counter remains ≥ 1.

**Escape hatch — `sweepRemainingAssets` (L402-403):**
```solidity
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```
`hasUnlockedWithdrawals` returns `unlockedWithdrawalsCount[asset] > 0` (L629-631). Because the stuck request keeps the counter non-zero, `sweepRemainingAssets` is permanently blocked. No other on-chain function can move the ETH.

The NatSpec on `completeWithdrawalForUser` (L191) even acknowledges the gap: *"Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH"* — yet the function still routes through the same reverting `_transferAsset` path.

## Impact Explanation
**Critical — Permanent freezing of funds.** After `unlockQueue` executes, the user's rsETH is irreversibly burned and the ETH equivalent is locked in `LRTWithdrawalManager` with no callable on-chain path to recover it. Both `completeWithdrawal` and `completeWithdrawalForUser` revert on every call, and `sweepRemainingAssets` is gated behind the non-zero `unlockedWithdrawalsCount`. Recovery requires a governance-driven contract upgrade.

## Likelihood Explanation
**Low-Medium.** The trigger condition — a contract address that cannot receive native ETH — is realistic and common in DeFi: plain multisigs (e.g., Gnosis Safe without a module), DAO treasuries, proxy contracts with no ETH handler, and institutional custodian contracts. `initiateWithdrawal` places no restriction on `msg.sender`, so any such contract can reach this state without any privileged action. The attacker need only hold rsETH and call `initiateWithdrawal`; the freeze is triggered automatically when the operator runs `unlockQueue`.

## Recommendation
Replace the hard-revert pattern in `_transferAsset` with a WETH-fallback or pull-payment pattern:

```solidity
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) {
            // Fallback: wrap as WETH and deliver as ERC-20
            IWETH(WETH_ADDRESS).deposit{ value: amount }();
            IERC20(WETH_ADDRESS).safeTransfer(to, amount);
        }
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
```

Alternatively, adopt a pull-payment pattern: on failed ETH transfer, record the amount in a `pendingEthClaims[user]` mapping and expose a `claimEth()` function, ensuring the queue counter and sweep path are never blocked by a single stuck transfer.

## Proof of Concept
1. Deploy `MaliciousReceiver` — a contract with `receive() external payable { revert(); }` that holds rsETH.
2. `MaliciousReceiver` calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned (L305); ETH moves from `LRTUnstakingVault` into `LRTWithdrawalManager` (L307). `unlockedWithdrawalsCount[ETH_TOKEN]` becomes 1.
4. `MaliciousReceiver` calls `completeWithdrawal(ETH_TOKEN, "")`. `_transferAsset` calls `payable(MaliciousReceiver).call{value: amount}("")`. `receive()` reverts → `sent == false` → `revert EthTransferFailed()`. All state changes (including `unlockedWithdrawalsCount--`) roll back.
5. Operator calls `completeWithdrawalForUser(ETH_TOKEN, MaliciousReceiver, "")`. Same revert path.
6. Manager calls `sweepRemainingAssets(ETH_TOKEN)`. Reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH_TOKEN] == 1`.
7. ETH is permanently frozen in `LRTWithdrawalManager`. rsETH is already burned. No on-chain recovery path exists. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L402-403)
```text
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

**File:** contracts/LRTWithdrawalManager.sol (L629-631)
```text
    function hasUnlockedWithdrawals(address asset) public view returns (bool hasUnlocked) {
        return unlockedWithdrawalsCount[asset] > 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-734)
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
