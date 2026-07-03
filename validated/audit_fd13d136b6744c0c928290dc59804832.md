### Title
Unbounded Gas Forwarding in ETH Push-Payment Enables Operator Gas Grief via `completeWithdrawalForUser` - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager._transferAsset` forwards all remaining gas when pushing ETH to a recipient via `.call{ value: amount }("")`. When an operator calls `completeWithdrawalForUser` for a user whose address is a contract with a gas-consuming `receive()` function, the operator's transaction can be made to consume unbounded gas or revert entirely.

### Finding Description
`_transferAsset` at line 878 uses a bare low-level call with no gas stipend:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
```

This call forwards all remaining gas to `to`. The function is invoked at line 734 inside `_processWithdrawalCompletion`, which is the shared internal path for both `completeWithdrawal` (user self-serves) and `completeWithdrawalForUser` (operator completes on behalf of a user).

`completeWithdrawalForUser` is a legitimate operational function — operators are expected to call it to unblock stuck withdrawals. Any unprivileged user can call `initiateWithdrawal(ETH_TOKEN, ...)` from a contract address. Once the withdrawal is unlocked, if an operator calls `completeWithdrawalForUser` for that contract address, the contract's `receive()` fallback executes with all remaining gas. A malicious `receive()` can loop over storage writes, consuming the entire gas budget and causing the operator's transaction to revert.

The developer comment at line 191 acknowledges this: *"Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH"* — but this dismissal is incorrect. A reverted operator transaction means the withdrawal state changes are rolled back, the operator loses gas fees, and the attacker's withdrawal remains pending (completable by the attacker themselves via `completeWithdrawal`). The attacker bears no cost beyond the initial `initiateWithdrawal` gas.

### Impact Explanation
**Medium — Unbounded gas consumption.** An operator calling `completeWithdrawalForUser` for a malicious ETH withdrawal recipient will have their transaction consume all forwarded gas. The operator loses gas fees on every such attempt. If the operator is running automated completion scripts, repeated griefing can impose sustained operational cost. The withdrawal queue for other users is unaffected, but the operator's ability to service ETH withdrawals on behalf of users is degraded.

### Likelihood Explanation
**Medium.** `initiateWithdrawal` is an unprivileged, publicly callable function. Any user can initiate an ETH withdrawal from a contract address. Operators are expected to call `completeWithdrawalForUser` as part of normal operations (e.g., to unblock users who cannot self-serve). The attacker needs only to deploy a contract with a gas-consuming `receive()` and initiate a withdrawal above `minRsEthAmountToWithdraw[ETH_TOKEN]`. No privileged access, no front-running, and no external dependency is required.

### Recommendation
Replace the push-payment ETH transfer in `_transferAsset` with a pull-payment model for ETH withdrawals:

1. Instead of calling `payable(to).call{ value: amount }("")` inside `_processWithdrawalCompletion`, store the owed amount in a `mapping(address => uint256) pendingETHWithdrawals`.
2. Add a separate `claimETH()` function that lets users pull their own ETH: `(bool sent,) = payable(msg.sender).call{ value: amount }("")`.

This mirrors the fix described in the external report and eliminates the ability of a recipient contract to grief the caller's transaction. For the `completeWithdrawalForUser` path specifically, a gas cap (e.g., `call{ value: amount, gas: 2300 }("")`) is an interim mitigation, but the pull model is the correct long-term fix.

### Proof of Concept
```
1. Attacker deploys MaliciousReceiver:
   receive() external payable {
       // consume all gas
       uint256 i;
       while (true) { i++; assembly { sstore(i, i) } }
   }

2. Attacker calls:
   LRTWithdrawalManager.initiateWithdrawal(
       ETH_TOKEN,
       minRsEthAmountToWithdraw[ETH_TOKEN],
       ""
   )
   // from MaliciousReceiver address (or on its behalf)

3. After the withdrawal delay passes and the operator unlocks the queue,
   operator calls:
   LRTWithdrawalManager.completeWithdrawalForUser(
       ETH_TOKEN,
       address(MaliciousReceiver),
       ""
   )

4. Execution path:
   completeWithdrawalForUser
     → _processWithdrawalCompletion(ETH_TOKEN, MaliciousReceiver, "")
       → _transferAsset(ETH_TOKEN, MaliciousReceiver, amount)
         → payable(MaliciousReceiver).call{ value: amount }("")
           → MaliciousReceiver.receive() consumes all remaining gas
             → operator tx reverts, operator loses all gas fees
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L730-738)
```text
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
