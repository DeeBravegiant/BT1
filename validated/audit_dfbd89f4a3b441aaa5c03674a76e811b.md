### Title
Unprotected `sendFunds()` in `FeeReceiver` Allows Anyone to Force MEV Reward Distribution at Arbitrary Times - (File: contracts/FeeReceiver.sol)

### Summary
`FeeReceiver.sendFunds()` is marked `external` with no access control modifier. Any unprivileged caller can invoke it at any time, forcing the entire ETH balance of the `FeeReceiver` contract (MEV/execution-layer rewards) into the `LRTDepositPool`. This mirrors the root cause of [C01]: an external function with no access controls that allows anyone to trigger a fund movement that should be protocol-controlled.

### Finding Description
The `FeeReceiver` contract accumulates MEV and execution-layer rewards via its `receive()` fallback. The `sendFunds()` function is intended to be called by the protocol to forward these rewards to the deposit pool, increasing TVL and the rsETH exchange rate. However, the function carries no `onlyRole`, `onlyOwner`, or any other access guard:

```solidity
// contracts/FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Every other state-mutating function in the same contract (`setDepositPool`) is gated behind `onlyRole(LRTConstants.MANAGER)`. `sendFunds()` is the sole exception.

The destination function `receiveFromRewardReceiver()` in `LRTDepositPool` is also unguarded (`external payable` with no modifier), so the call chain completes without any check.

### Impact Explanation
When `sendFunds()` is called, the entire ETH balance of `FeeReceiver` is injected into the deposit pool, immediately increasing the protocol's reported TVL and thus the rsETH/ETH exchange rate. Any caller can trigger this at will:

- **Rate manipulation at deposit time**: An attacker who holds rsETH can call `sendFunds()` immediately before a large user deposit. The inflated rate causes the depositor to receive fewer rsETH tokens than they would have received had the rewards been distributed on the protocol's schedule. The attacker's existing rsETH position appreciates at the depositor's expense.
- **Loss of protocol control over reward timing**: The protocol cannot batch, delay, or condition reward distribution (e.g., to coincide with oracle updates or paused states). The contract fails to deliver its promised behavior of controlled reward forwarding.

Impact classification: **Low** — the contract fails to deliver promised returns (controlled MEV distribution), but no ETH is lost from the system; it is merely redistributed earlier than intended.

### Likelihood Explanation
The function is unconditionally callable by any EOA or contract. No preconditions, no cost beyond gas. The `FeeReceiver` contract continuously accumulates ETH from validator MEV/EL rewards, so there will routinely be a non-zero balance available to trigger. Likelihood is **High**.

### Recommendation
Add an access control modifier to `sendFunds()` consistent with the rest of the contract's privileged functions:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

### Proof of Concept
1. `FeeReceiver` accumulates ETH rewards (e.g., 10 ETH from MEV).
2. Attacker holds rsETH and observes a large pending deposit in the mempool.
3. Attacker calls `FeeReceiver.sendFunds()` — no role check, succeeds immediately.
4. `LRTDepositPool.receiveFromRewardReceiver()` receives 10 ETH, increasing TVL.
5. rsETH/ETH rate increases before the victim's deposit is processed.
6. Victim's deposit mints fewer rsETH tokens than expected at the pre-manipulation rate.
7. Attacker's existing rsETH is now worth proportionally more. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/FeeReceiver.sol (L52-58)
```text
    /// @dev send all rewards to deposit pool
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/FeeReceiver.sol (L64-72)
```text
    /// @dev Set the deposit pool
    /// @param _depositPool Address of the deposit pool
    function setDepositPool(address _depositPool) external onlyRole(LRTConstants.MANAGER) {
        if (_depositPool == address(0)) revert InvalidEmptyValue();

        depositPool = _depositPool;

        emit DepositPoolSet(_depositPool);
    }
```

**File:** contracts/LRTDepositPool.sol (L60-61)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }
```
