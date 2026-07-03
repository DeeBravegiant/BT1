### Title
NodeDelegator Removal Permanently Blockable via Dust Token/ETH Transfer - (File: contracts/LRTDepositPool.sol)

### Summary
`removeNodeDelegatorContractFromQueue` reverts if the target NodeDelegator (NDC) holds any ETH or LST balance above `maxNegligibleAmount`. Because the NDC's `receive()` function accepts ETH from any caller, and ERC-20 tokens can be transferred to any address permissionlessly, an unprivileged attacker can permanently prevent the admin from removing any NDC from the queue by maintaining a non-zero balance on it.

### Finding Description
`LRTDepositPool.removeNodeDelegatorContractFromQueue` delegates to the internal `_removeNodeDelegatorContractFromQueue`, which calls two balance-guard helpers before performing the removal:

- `_checkResidueEthBalance` reverts with `NodeDelegatorHasETH` if `address(nodeDelegatorAddress).balance > maxNegligibleAmount`.
- `_checkResidueLSTBalance` reverts with `NodeDelegatorHasAssetBalance` if `IERC20(supportedAssets[i]).balanceOf(nodeDelegatorAddress) > maxNegligibleAmount` for any supported LST. [1](#0-0) [2](#0-1) [3](#0-2) 

`maxNegligibleAmount` is a storage variable that defaults to `0` (Solidity zero-initialisation) and is only raised by an explicit admin call to `setMaxNegligibleAmount`. [4](#0-3) [5](#0-4) 

The NDC exposes an unrestricted `receive()` function, so any external account can push ETH into it at will: [6](#0-5) 

ERC-20 tokens (stETH, ETHx, etc.) can similarly be transferred to the NDC address by any holder without any on-chain restriction.

### Impact Explanation
An attacker who wishes to keep a specific NDC in the queue indefinitely — for example to prevent the protocol from migrating to a new delegation operator, to block a response to a compromised NDC, or to lock the queue at its current composition — can do so at negligible cost. Every time the admin drains the NDC to zero (a prerequisite for removal), the attacker re-sends 1 wei of ETH or 1 unit of any supported LST. The removal call reverts unconditionally. Because the NDC cannot be dequeued, the protocol cannot restructure its delegation infrastructure, and any funds that must flow through a new NDC remain inaccessible through the intended path.

**Impact: Medium — Temporary (practically permanent) freezing of funds / administrative capability.**

### Likelihood Explanation
The attack requires no privilege, no front-running on chains with private mempools, and costs only dust amounts of ETH or LST. The attacker simply monitors the NDC balance off-chain and re-sends dust whenever it reaches zero. Any user with a grievance against the protocol (e.g., a competing operator, a user who wants to keep a particular delegation operator in place) has both the means and the incentive to execute this indefinitely.

### Recommendation
Mirror the fix applied to the analogous YRizStrategy issue: instead of reverting when the NDC has a residual balance, automatically sweep the residual balance back to the deposit pool (or to the unstaking vault) as part of the removal transaction. This eliminates the externally controllable revert condition entirely.

Concretely, replace the `_checkResidueEthBalance` / `_checkResidueLSTBalance` revert guards with withdrawal logic that pulls any remaining ETH and LST out of the NDC before dequeuing it, so that the removal is always atomic and cannot be blocked by a dust deposit.

### Proof of Concept

1. Admin decides to remove `ndcA` from the queue (e.g., to migrate to a new operator).
2. Admin (or the protocol) drains `ndcA` to zero ETH and zero LST balance — a necessary precondition.
3. Attacker calls `(bool ok,) = payable(ndcA).call{value: 1}("")` — costs 1 wei.
4. Admin calls `LRTDepositPool.removeNodeDelegatorContractFromQueue(ndcA)`.
5. Inside `_removeNodeDelegatorContractFromQueue`, `_checkResidueEthBalance` evaluates `address(ndcA).balance > maxNegligibleAmount` → `1 > 0` → `true` → reverts with `NodeDelegatorHasETH`.
6. Attacker repeats step 3 every time the admin drains the NDC, blocking removal indefinitely at a cost of 1 wei per attempt. [7](#0-6) [8](#0-7) [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L36-36)
```text
    uint256 public maxNegligibleAmount;
```

**File:** contracts/LRTDepositPool.sol (L274-277)
```text
    function setMaxNegligibleAmount(uint256 maxNegligibleAmount_) external onlyLRTAdmin {
        maxNegligibleAmount = maxNegligibleAmount_;
        emit MaxNegligibleAmountUpdated(maxNegligibleAmount_);
    }
```

**File:** contracts/LRTDepositPool.sol (L328-330)
```text
    function removeNodeDelegatorContractFromQueue(address nodeDelegatorAddress) external onlyLRTAdmin {
        _removeNodeDelegatorContractFromQueue(nodeDelegatorAddress);
    }
```

**File:** contracts/LRTDepositPool.sol (L579-597)
```text
    function _removeNodeDelegatorContractFromQueue(address nodeDelegatorAddress) internal {
        // 1. check if node delegator contract is in queue and find Index
        uint256 ndcIndex = _getNDCIndex(nodeDelegatorAddress);

        // 2. revert if node delegator contract has any asset balances.
        // 2.1 check if NDC has native ETH balance in eigen layer or/and in itself.
        _checkResidueEthBalance(nodeDelegatorAddress);
        // 2.2  check if NDC has LST balance
        _checkResidueLSTBalance(nodeDelegatorAddress);

        // 3. remove node delegator contract from queue
        // 3.1 remove from isNodeDelegator mapping
        isNodeDelegator[nodeDelegatorAddress] = 0;
        // 3.2 remove from nodeDelegatorQueue
        nodeDelegatorQueue[ndcIndex] = nodeDelegatorQueue[nodeDelegatorQueue.length - 1];
        nodeDelegatorQueue.pop();

        emit NodeDelegatorRemovedFromQueue(nodeDelegatorAddress);
    }
```

**File:** contracts/LRTDepositPool.sol (L616-624)
```text
    function _checkResidueEthBalance(address nodeDelegatorAddress) internal view {
        if (
            INodeDelegator(nodeDelegatorAddress).getEffectivePodShares() != 0
                || address(nodeDelegatorAddress).balance > maxNegligibleAmount
                || INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(LRTConstants.ETH_TOKEN) > 0
        ) {
            revert NodeDelegatorHasETH();
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L627-646)
```text
    function _checkResidueLSTBalance(address nodeDelegatorAddress) internal view {
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetsLength = supportedAssets.length;

        uint256 assetBalance;
        for (uint256 i; i < supportedAssetsLength; ++i) {
            if (supportedAssets[i] == LRTConstants.ETH_TOKEN) {
                // this function only checks for residual LST balance
                continue;
            }

            assetBalance = IERC20(supportedAssets[i]).balanceOf(nodeDelegatorAddress)
                + INodeDelegator(nodeDelegatorAddress).getAssetBalance(supportedAssets[i]);
            assetBalance += INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(supportedAssets[i]);

            if (assetBalance > maxNegligibleAmount) {
                revert NodeDelegatorHasAssetBalance(supportedAssets[i], assetBalance);
            }
        }
    }
```

**File:** contracts/NodeDelegator.sol (L81-83)
```text
    receive() external payable {
        emit ETHReceived(msg.sender, msg.value);
    }
```
