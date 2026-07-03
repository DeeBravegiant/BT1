### Title
Gas Griefing via Unsafe ETH Transfer in `_transferAsset` Allows Operator Gas Theft - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager._transferAsset` uses the Solidity `(bool sent,)` pattern for ETH transfers, which silently copies all return data from the recipient's fallback into memory. A malicious withdrawer can deploy a contract with a fallback that returns an arbitrarily large payload, then queue an ETH withdrawal from that contract. When an operator calls `completeWithdrawalForUser` to finalize the withdrawal, the return data copy causes unbounded gas consumption at the operator's expense.

### Finding Description
`_transferAsset` performs ETH transfers using:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
``` [1](#0-0) 

In Solidity, `(bool sent,)` is syntactically equivalent to `(bool sent, bytes memory data)`. Even though the second return variable is discarded, the EVM still copies all return data from the callee into memory before the assignment. Memory expansion in the EVM is priced quadratically, so a recipient that returns a large payload forces the caller to pay for the full memory allocation cost.

This internal function is invoked from `completeWithdrawalForUser`, which is callable by any `LRT_OPERATOR`:

```solidity
function completeWithdrawalForUser(address asset, address user, ...) external nonReentrant whenNotPaused onlyLRTOperator {
    _processWithdrawalCompletion(asset, user, referralId);
``` [2](#0-1) 

`_processWithdrawalCompletion` ultimately calls `_transferAsset(asset, user, request.expectedAssetAmount)`: [3](#0-2) 

The `user` address is set at `initiateWithdrawal` time and is fully attacker-controlled — any `msg.sender` (including a contract) can call `initiateWithdrawal`: [4](#0-3) 

The developer comment on `completeWithdrawalForUser` acknowledges the scenario but incorrectly dismisses it:

> `@dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH` [5](#0-4) 

The function has no restriction preventing its use with ETH, so the dismissal is incorrect — the operator is the transaction sender and pays all gas, including the cost of copying the malicious return payload.

### Impact Explanation
An operator calling `completeWithdrawalForUser` for a malicious ETH withdrawal request will have an unbounded amount of gas consumed due to memory expansion costs from copying the recipient's large return payload. This constitutes direct, repeatable gas theft from the operator. The operator cannot distinguish a malicious request from a legitimate one before executing it.

**Impact: Medium — Unbounded gas consumption.**

### Likelihood Explanation
Any unprivileged rsETH holder can call `initiateWithdrawal` from a contract address. The attack requires no special permissions, no front-running, and no external protocol dependency. The only prerequisite is that an operator calls `completeWithdrawalForUser` for the malicious request, which is a routine operational action. Likelihood is **Medium**.

### Recommendation
Replace the Solidity-level call with an assembly `call` that explicitly sets the output buffer size to zero, preventing any return data from being copied to memory:

```solidity
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        bool sent;
        assembly {
            sent := call(gas(), to, amount, 0, 0, 0, 0)
        }
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
```

The same fix should be applied to `_collectInterestToTreasury` at line 957 for consistency, even though `treasury` is currently a trusted address. [6](#0-5) 

### Proof of Concept

1. Attacker deploys `MaliciousReceiver`:
```solidity
contract MaliciousReceiver {
    // Queue ETH withdrawal from this contract
    function attack(address withdrawalManager, address rsETH, uint256 amount) external {
        IERC20(rsETH).approve(withdrawalManager, amount);
        ILRTWithdrawalManager(withdrawalManager).initiateWithdrawal(ETH_TOKEN, amount, "");
    }

    // Returns 500 KB of data on ETH receipt
    fallback() external payable {
        assembly {
            return(0, 500000)
        }
    }
}
```

2. Attacker acquires rsETH, calls `attack(...)`. The withdrawal request is now queued with `user = address(MaliciousReceiver)`.

3. After the unlock delay, an operator calls:
```solidity
withdrawalManager.completeWithdrawalForUser(ETH_TOKEN, address(maliciousReceiver), "");
```

4. Inside `_transferAsset`, the call `payable(maliciousReceiver).call{ value: amount }("")` triggers the fallback, which returns 500 KB. Solidity copies all 500 KB into memory. The operator's transaction consumes a massive amount of gas — far beyond what a normal ETH withdrawal costs — with the excess paid entirely by the operator.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-166)
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

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L876-879)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
```

**File:** contracts/LRTWithdrawalManager.sol (L957-958)
```text
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
```
