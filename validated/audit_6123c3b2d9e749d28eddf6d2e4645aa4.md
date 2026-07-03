### Title
Payable Receive Functions Without Access Control Allow Permanent ETH Loss - (File: contracts/LRTDepositPool.sol)

### Summary
Three payable functions in `LRTDepositPool.sol` — `receiveFromRewardReceiver()`, `receiveFromLRTConverter()`, and `receiveFromNodeDelegator()` — have no access control. Any unprivileged user can call them with ETH attached. The ETH is silently absorbed into the protocol's accounting and permanently lost to the caller, who receives no rsETH in return.

### Finding Description
`LRTDepositPool.sol` exposes three named payable functions intended to receive ETH from specific internal protocol contracts:

```solidity
function receiveFromRewardReceiver() external payable { }   // line 61
function receiveFromLRTConverter()   external payable { }   // line 64
function receiveFromNodeDelegator()  external payable { }   // line 67
```

None of these functions carry any access control modifier. Their bodies are completely empty — they never read or act on `msg.value`. Any external caller can invoke them with ETH attached. The ETH is silently added to `address(this).balance`.

`LRTDepositPool.getETHDistributionData()` computes `ethLyingInDepositPool = address(this).balance`, which feeds directly into `getTotalAssetDeposits(LRTConstants.ETH_TOKEN)`. That total is used by `getRsETHAmountToMint()` to price rsETH. ETH donated this way inflates the protocol's ETH accounting, raises the rsETH price, and benefits existing rsETH holders — but the sender receives nothing and has no recovery path.

An identical pattern exists in `LRTWithdrawalManager.sol`:

```solidity
function receiveFromLRTUnstakingVault() external payable { }  // line 138
```

ETH sent here is absorbed into the withdrawal manager's balance and used to service other users' withdrawals, again with no benefit to the sender.

### Impact Explanation
ETH sent to any of these functions is permanently frozen from the sender's perspective. The caller loses their ETH with no rsETH minted and no refund mechanism. The lost ETH is redistributed to existing rsETH holders via an inflated rsETH price, constituting a permanent, irrecoverable loss of user funds. This matches the "permanent freezing of funds" impact class.

### Likelihood Explanation
Low. A user must explicitly call one of these named functions with ETH attached rather than using the standard `depositETH()` entry point. However, no on-chain guard prevents it, and the functions are publicly callable with no revert path, making accidental or confused usage possible (e.g., via a script or wallet integration that resolves function selectors incorrectly).

### Recommendation
Add caller-restriction checks to each function, mirroring the pattern already used in `NodeDelegator.sendETHFromDepositPoolToNDC()`:

```solidity
function receiveFromRewardReceiver() external payable {
    if (msg.sender != lrtConfig.getContract(LRTConstants.LRT_REWARD_RECEIVER))
        revert InvalidETHSender();
}
function receiveFromLRTConverter() external payable {
    if (msg.sender != lrtConfig.getContract(LRTConstants.LRT_CONVERTER))
        revert InvalidETHSender();
}
function receiveFromNodeDelegator() external payable {
    if (isNodeDelegator[msg.sender] != 1)
        revert InvalidETHSender();
}
```

Apply the same fix to `LRTWithdrawalManager.receiveFromLRTUnstakingVault()`.

### Proof of Concept
1. Attacker (or confused user) calls `LRTDepositPool.receiveFromRewardReceiver{value: 10 ether}()`.
2. No revert occurs; 10 ETH is added to `address(this).balance`.
3. `getETHDistributionData()` returns `ethLyingInDepositPool = address(this).balance` (now includes the 10 ETH).
4. `getTotalAssetDeposits(ETH_TOKEN)` increases by 10 ETH.
5. `getRsETHAmountToMint()` computes a higher rsETH price; existing rsETH holders gain value.
6. The caller's 10 ETH is permanently lost — no rsETH is minted, no refund is possible. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L61-67)
```text
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTWithdrawalManager.sol (L138-138)
```text
    function receiveFromLRTUnstakingVault() external payable { }
```

**File:** contracts/NodeDelegator.sol (L445-452)
```text
    function sendETHFromDepositPoolToNDC() external payable override {
        // only allow LRT deposit pool to send ETH to this contract
        if (msg.sender != lrtConfig.depositPool()) {
            revert InvalidETHSender();
        }

        emit ETHDepositFromDepositPool(msg.value);
    }
```
