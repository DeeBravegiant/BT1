### Title
ETH Withdrawal Permanently Frozen When Recipient Contract Reverts on Native ETH Receipt - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` sends ETH to the withdrawing user via a low-level `.call{value:}` in `_transferAsset`. If the recipient is a contract whose `receive()` function reverts (or has no `receive()` at all), every call to `completeWithdrawal` or `completeWithdrawalForUser` will revert. Because the user's rsETH is already burned and the ETH is already sitting in `LRTWithdrawalManager` (moved there by a prior `unlockQueue` call), the ETH becomes permanently unrecoverable with no protocol-level escape hatch.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is split across two separate transactions:

**Step 1 — `initiateWithdrawal`**: The user's rsETH is pulled into `LRTWithdrawalManager`.

**Step 2 — `unlockQueue` (operator-only)**: The rsETH held by the contract is burned, and the corresponding ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`. [1](#0-0) 

After Step 2, the rsETH is gone and the ETH is in `LRTWithdrawalManager`.

**Step 3 — `completeWithdrawal` / `completeWithdrawalForUser`**: Calls `_processWithdrawalCompletion`, which ends with: [2](#0-1) 

`_transferAsset` for ETH is: [3](#0-2) 

If `to` is a contract that reverts on ETH receipt, `sent` is `false`, and the function reverts with `EthTransferFailed`. Because the entire transaction reverts, all state mutations in `_processWithdrawalCompletion` (the `popFront`, the `delete`, the `unlockedWithdrawalsCount--`) are also rolled back. The withdrawal request remains in the unlocked queue indefinitely.

The only protocol-level recovery function is `sweepRemainingAssets`, but it is gated by: [4](#0-3) 

Because `unlockedWithdrawalsCount[ETH_TOKEN] > 0` (the stuck request keeps it non-zero), `sweepRemainingAssets` is permanently blocked. The ETH sits in the contract with no callable path to move it.

The operator-facing `completeWithdrawalForUser` suffers the same revert — its own NatSpec even acknowledges the gap: [5](#0-4) 

---

### Impact Explanation

**Permanent freezing of funds (Critical).** After `unlockQueue` executes:
- The user's rsETH is irreversibly burned.
- The ETH equivalent is in `LRTWithdrawalManager`.
- Neither `completeWithdrawal` nor `completeWithdrawalForUser` can deliver the ETH.
- `sweepRemainingAssets` is blocked by the non-zero `unlockedWithdrawalsCount`.
- No other on-chain path exists to move the ETH without a contract upgrade.

The user loses both their rsETH and their ETH with no recourse short of a governance-driven contract upgrade.

---

### Likelihood Explanation

**Low-Medium.** Any contract address that:
- Has no `receive()` or `fallback()` function (e.g., a plain multisig, a DAO treasury, a proxy with no ETH handler), or
- Has a `receive()` that explicitly reverts,

will trigger this freeze the moment it initiates an ETH withdrawal. DeFi protocols, DAOs, and institutional custodians routinely interact with restaking protocols from contract addresses. The protocol places no restriction on `msg.sender` in `initiateWithdrawal`, so any such contract can reach this state without any privileged action.

---

### Recommendation

Replace the hard-revert pattern in `_transferAsset` with a pull-payment or WETH-fallback pattern analogous to the Gearbox fix:

```solidity
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) {
            // Fallback: wrap as WETH and send ERC-20 instead
            IWETH(WETH_ADDRESS).deposit{ value: amount }();
            IERC20(WETH_ADDRESS).safeTransfer(to, amount);
        }
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
```

Alternatively, adopt a pull-payment pattern: record the failed amount in a `pendingEthClaims[user]` mapping and expose a separate `claimEth()` function, so a stuck transfer never blocks the queue counter or the sweep path.

---

### Proof of Concept

1. Attacker deploys `MaliciousReceiver` — a contract with `receive() external payable { revert(); }`.
2. `MaliciousReceiver` calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned; ETH moves from `LRTUnstakingVault` into `LRTWithdrawalManager`. `unlockedWithdrawalsCount[ETH_TOKEN]` becomes 1.
4. `MaliciousReceiver` calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` calls `payable(MaliciousReceiver).call{value: amount}("")`. `MaliciousReceiver.receive()` reverts → `sent == false` → `revert EthTransferFailed()`. All state changes roll back.
5. Operator calls `completeWithdrawalForUser(ETH_TOKEN, MaliciousReceiver, "")`. Same revert.
6. Manager calls `sweepRemainingAssets(ETH_TOKEN)`. Reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH_TOKEN] == 1`.
7. ETH is permanently frozen in `LRTWithdrawalManager`. User's rsETH is already burned. No on-chain recovery path exists. [3](#0-2) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
```

**File:** contracts/LRTWithdrawalManager.sol (L301-307)
```text
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

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

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
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
