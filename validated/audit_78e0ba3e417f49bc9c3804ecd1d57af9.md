### Title
Front-Running `completeUnstaking` via Direct EigenLayer `completeQueuedWithdrawal` Call Freezes Withdrawal Tokens in NodeDelegator — (`contracts/NodeDelegator.sol`)

---

### Summary

`NodeDelegator.completeUnstaking` uses a before/after balance snapshot to measure how many tokens were received from EigenLayer and then forwards that delta to the `LRTUnstakingVault`. Because EigenLayer's `DelegationManager.completeQueuedWithdrawal` is permissionless (callable by anyone), an attacker can front-run the operator's `completeUnstaking` call, causing the EigenLayer withdrawal to be consumed before the NDC's own call executes. The NDC's subsequent call to `completeQueuedWithdrawal` then reverts, leaving the withdrawal tokens stranded in the `NodeDelegator` and the `uncompletedWithdrawalCount` permanently inflated.

---

### Finding Description

`NodeDelegator.completeUnstaking` captures balances before calling EigenLayer, then transfers the difference to the unstaking vault: [1](#0-0) 

```solidity
uint256[] memory balancesBefore = getBalances(assets);

_getDelegationManager().completeQueuedWithdrawal(withdrawal, assets, receiveAsTokens);

_getUnstakingVault().decreaseUncompletedWithdrawalCount();

if (receiveAsTokens) {
    for (uint256 i; i < assetCount; i++) {
        ...
        assets[i].safeTransfer(
            address(_getUnstakingVault()), assets[i].balanceOf(address(this)) - balancesBefore[i]
        );
    }
}
```

EigenLayer's `DelegationManager.completeQueuedWithdrawal` is a permissionless external function — anyone may call it for any queued withdrawal once the delay has elapsed. The `withdrawer` field in the `Withdrawal` struct is `address(this)` (the NDC), so tokens are sent directly to the NDC when called externally.

**Attack path:**

1. Operator queues a withdrawal via `initiateUnstaking`; the withdrawal delay elapses.
2. Attacker observes the operator's pending `completeUnstaking` transaction in the mempool.
3. Attacker front-runs by calling `DelegationManager.completeQueuedWithdrawal(withdrawal, assets, true)` directly on EigenLayer.
4. EigenLayer transfers the LST/ETH tokens to the NDC and marks the withdrawal root as consumed.
5. The operator's `completeUnstaking` executes: `getBalances` captures the already-inflated balance as `balancesBefore`, then `_getDelegationManager().completeQueuedWithdrawal(...)` **reverts** because the withdrawal root no longer exists.
6. The entire `completeUnstaking` transaction reverts.
7. Tokens are stranded in the NDC; `decreaseUncompletedWithdrawalCount` is never called. [2](#0-1) 

---

### Impact Explanation

- **Tokens frozen in NodeDelegator**: The LST/ETH tokens that EigenLayer sent to the NDC never reach `LRTUnstakingVault`. Users with pending withdrawal requests in `LRTWithdrawalManager` cannot complete them because the vault has no funds.
- **`uncompletedWithdrawalCount` permanently inflated**: `decreaseUncompletedWithdrawalCount` is never called. If the attacker repeats this for every queued withdrawal, the counter reaches `maxUncompletedWithdrawalCount`, blocking all future calls to `initiateUnstaking` and `undelegate`. [3](#0-2) 

The only recovery path is an admin calling `transferBackToLRTDepositPool`, which routes tokens to the deposit pool — not the unstaking vault — leaving withdrawal requests unfulfillable until manual re-routing. [4](#0-3) 

**Impact class**: Temporary (potentially extended) freezing of user withdrawal funds; permanent inflation of `uncompletedWithdrawalCount` per griefed withdrawal.

---

### Likelihood Explanation

- EigenLayer's `completeQueuedWithdrawal` is publicly callable with no `msg.sender` restriction.
- The withdrawal struct (staker, nonce, strategies, shares) is fully observable on-chain once `initiateUnstaking` is called.
- No special capital or permissions are required; gas cost is the only barrier.
- The attack can be repeated for every withdrawal the operator attempts to complete.

---

### Recommendation

Replace the before/after balance delta pattern with a direct transfer of the full current balance (minus any pre-existing balance that was already present before the withdrawal was queued), **or** record the expected token amounts at queue time and transfer exactly those amounts from the NDC's current balance regardless of when EigenLayer delivered them:

```solidity
// Instead of:
uint256[] memory balancesBefore = getBalances(assets);
_getDelegationManager().completeQueuedWithdrawal(...);
// transfer balanceAfter - balanceBefore

// Consider:
// 1. Try/catch the completeQueuedWithdrawal call and handle the already-completed case gracefully.
// 2. Or: record expected amounts at queue time and transfer the full expected amount
//    from whatever balance is present in the NDC, regardless of when EigenLayer delivered it.
```

Alternatively, track the expected withdrawal amounts in storage at `initiateUnstaking` time and, in `completeUnstaking`, transfer those stored amounts from the NDC's current balance (which will be present whether EigenLayer was called by the NDC or by a third party).

---

### Proof of Concept

1. Operator calls `NodeDelegator.initiateUnstaking([stETHStrategy], [1000e18])` → withdrawal queued, `uncompletedWithdrawalCount` = 1.
2. EigenLayer delay passes (7 days).
3. Attacker calls `DelegationManager.completeQueuedWithdrawal(withdrawal, [stETH], true)` directly → 1000 stETH sent to NDC, withdrawal root deleted.
4. Operator calls `NodeDelegator.completeUnstaking(withdrawal, [stETH], true)` → `_getDelegationManager().completeQueuedWithdrawal(...)` reverts → entire tx reverts.
5. 1000 stETH sits in NDC; `LRTUnstakingVault` balance unchanged; `uncompletedWithdrawalCount` remains 1 forever for this withdrawal.
6. Users with stETH withdrawal requests in `LRTWithdrawalManager` cannot call `completeWithdrawal` (vault has no funds). [5](#0-4)

### Citations

**File:** contracts/NodeDelegator.sol (L303-330)
```text
    {
        if (_getUnstakingVault().uncompletedWithdrawalCount() >= _getUnstakingVault().maxUncompletedWithdrawalCount()) {
            revert MaxUncompletedWithdrawalsReached();
        }
        if (strategies.length == 0) {
            revert ZeroLengthArray();
        }

        if (strategies.length != shares.length) {
            revert ArrayLengthMismatch();
        }

        for (uint256 i = 0; i < strategies.length; i++) {
            if (!NodeDelegatorHelper.isSupportedStrategy(lrtConfig, strategies[i])) {
                revert StrategyIsNotSetForAsset();
            }
        }

        IDelegationManager.QueuedWithdrawalParams[] memory queuedWithdrawalParams =
            new IDelegationManager.QueuedWithdrawalParams[](1);
        queuedWithdrawalParams[0] = IDelegationManagerTypes.QueuedWithdrawalParams({
            strategies: strategies, depositShares: shares, withdrawer: address(this)
        });

        bytes32[] memory withdrawalRoots = _getDelegationManager().queueWithdrawals(queuedWithdrawalParams);
        withdrawalRoot = withdrawalRoots[0];
        _getUnstakingVault().increaseUncompletedWithdrawalCount();
        emit WithdrawalQueued(_getNonce() - 1, address(this), withdrawalRoots);
```

**File:** contracts/NodeDelegator.sol (L346-400)
```text
    function completeUnstaking(
        IDelegationManager.Withdrawal calldata withdrawal,
        IERC20[] calldata assets,
        bool receiveAsTokens
    )
        public
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        if (withdrawal.staker != address(this)) {
            revert InvalidWithdrawalStaker();
        }

        uint256 assetCount = assets.length;
        if (assetCount == 0 || assetCount != withdrawal.scaledShares.length) {
            // asset length and strategies length is checked by eigenlayer contracts in `completeQueuedWithdrawal`
            revert InvalidWithdrawalData();
        }

        for (uint256 i; i < assetCount; i++) {
            if (lrtConfig.beaconChainETHStrategy() == address(withdrawal.strategies[i])) {
                if (address(assets[i]) != LRTConstants.ETH_TOKEN) {
                    revert StrategyAndAssetTokenMismatch();
                }
                continue;
            }

            if (address(assets[i]) != address(withdrawal.strategies[i].underlyingToken())) {
                revert StrategyAndAssetTokenMismatch();
            }
        }

        uint256[] memory balancesBefore = getBalances(assets);

        // Finalize withdrawal with Eigenlayer Delegation Manager
        _getDelegationManager().completeQueuedWithdrawal(withdrawal, assets, receiveAsTokens);

        _getUnstakingVault().decreaseUncompletedWithdrawalCount();

        if (receiveAsTokens) {
            for (uint256 i; i < assetCount; i++) {
                if (address(assets[i]) == LRTConstants.ETH_TOKEN) {
                    emit EthTransferred(address(_getUnstakingVault()), address(this).balance - balancesBefore[i]);
                    _getUnstakingVault().receiveFromNodeDelegator{ value: address(this).balance - balancesBefore[i] }();
                } else {
                    assets[i].safeTransfer(
                        address(_getUnstakingVault()), assets[i].balanceOf(address(this)) - balancesBefore[i]
                    );
                }
            }
        }

        emit EigenLayerWithdrawalCompleted(withdrawal.staker, withdrawal.nonce, msg.sender);
    }
```

**File:** contracts/NodeDelegator.sol (L467-488)
```text
    function transferBackToLRTDepositPool(
        address asset,
        uint256 amount
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlyAssetTransferRole
    {
        address lrtDepositPool = lrtConfig.depositPool();

        if (asset == LRTConstants.ETH_TOKEN) {
            ILRTDepositPool(lrtDepositPool).receiveFromNodeDelegator{ value: amount }();

            emit EthTransferred(lrtDepositPool, amount);
        } else {
            IERC20(asset).safeTransfer(lrtDepositPool, amount);

            emit AssetTransferred(asset, lrtDepositPool, amount);
        }
    }
```
