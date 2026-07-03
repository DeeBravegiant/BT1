The code is present and the call chain is exactly as described. Let me confirm the exact lines.

The vulnerability is confirmed and concrete. The call chain is exactly as described, with no guards that prevent the revert from propagating.

---

### Title
`setAaveIntegrationEnabled(false)` permanently blocked when treasury contract reverts on ETH receive - (`contracts/LRTWithdrawalManager.sol`)

### Summary
When disabling Aave integration, `setAaveIntegrationEnabled(false)` unconditionally calls `_collectInterestToTreasury()`, which withdraws accrued interest from Aave and then pushes it to the treasury via a raw ETH call. If the treasury is a smart contract that reverts on ETH receive, the entire transaction reverts and `isAaveIntegrationEnabled` is permanently stuck at `true`. The emergency escape hatch (`emergencyWithdrawFromAave`) has the same blocking call, so there is no alternative path to disable the integration or recover principal.

### Finding Description

`setAaveIntegrationEnabled(false)` executes this sequence unconditionally when `aaveBalance > 0`: [1](#0-0) 

Inside `_collectInterestToTreasury`, after the ETH has already been withdrawn from Aave into the contract, the treasury push is: [2](#0-1) 

If `payable(treasury).call{value: interestAmount}("")` returns `false` (treasury contract reverts on receive), the function reverts with `TreasuryTransferFailed`. Because this is inside the same transaction as `setAaveIntegrationEnabled`, the state write `isAaveIntegrationEnabled = false` at line 503 is never reached. [3](#0-2) 

The emergency path is equally blocked — `emergencyWithdrawFromAave` also calls `_collectInterestToTreasury()` before withdrawing principal: [4](#0-3) 

There is no code path that allows disabling Aave integration or withdrawing principal while bypassing the treasury push.

### Impact Explanation
The protocol is permanently unable to disable Aave integration while interest > 0 and the treasury cannot receive ETH. This means:
- ETH principal deposited to Aave cannot be recovered to service user withdrawals via the normal disable path.
- The integration cannot be turned off even in response to an Aave incident.
- Matches **Low: Contract fails to deliver promised returns, but doesn't lose value** — funds remain in Aave but the protocol cannot fulfill its operational guarantee of being able to disable the integration and return ETH to the withdrawal pool.

### Likelihood Explanation
Smart contract treasuries (Gnosis Safe multisigs, DAO vaults, proxy contracts without a `receive()` fallback) are common in production deployments. The treasury address is set via `lrtConfig`, and there is no validation that it can accept ETH. Interest accrues automatically over time in Aave, so the blocking condition (`aaveBalance > totalETHDepositedToAave`) is reached in normal operation without any attacker action.

### Recommendation
Decouple interest collection from the disable operation. Options:
1. Wrap the treasury push in a `try/catch` or check-and-skip pattern inside `_collectInterestToTreasury`, leaving uncollected interest in the contract rather than reverting.
2. Allow `setAaveIntegrationEnabled(false)` to proceed even if interest collection fails, emitting an event so the interest can be collected separately once the treasury issue is resolved.
3. Validate that the treasury address can receive ETH (e.g., send 0 wei) when it is first configured in `lrtConfig`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

// Mock treasury that reverts on ETH receive
contract RevertingTreasury {
    receive() external payable { revert("no ETH"); }
}

// Test (pseudocode, adapt to project's test harness):
// 1. Deploy LRTWithdrawalManager with Aave integration configured.
// 2. Set PROTOCOL_TREASURY in lrtConfig to address(new RevertingTreasury()).
// 3. Deposit ETH to Aave via unlockQueue / depositIdleETHToAave.
// 4. Advance time so aaveAWETH.balanceOf(withdrawalManager) > totalETHDepositedToAave.
// 5. Call setAaveIntegrationEnabled(false) as LRT manager.
// 6. Assert: transaction reverts with TreasuryTransferFailed.
// 7. Assert: isAaveIntegrationEnabled == true (unchanged).
// 8. Call emergencyWithdrawFromAave(type(uint256).max) as PAUSER_ROLE.
// 9. Assert: also reverts with TreasuryTransferFailed.
// Conclusion: no code path can disable integration or recover principal.
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L486-497)
```text
        if (!enabled) {
            uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
            if (aaveBalance > 0) {
                // First collect any accrued interest to treasury
                _collectInterestToTreasury();

                // Then withdraw remaining principal from Aave back to contract
                aaveBalance = aaveAWETH.balanceOf(address(this));
                if (aaveBalance > 0) {
                    _withdrawFromAave(aaveBalance);
                }
            }
```

**File:** contracts/LRTWithdrawalManager.sol (L503-504)
```text
        isAaveIntegrationEnabled = enabled;
        emit AaveIntegrationEnabled(enabled);
```

**File:** contracts/LRTWithdrawalManager.sol (L557-558)
```text
        // First collect any accrued interest to treasury
        _collectInterestToTreasury();
```

**File:** contracts/LRTWithdrawalManager.sol (L954-958)
```text
        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
```
