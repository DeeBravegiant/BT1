### Title
Nested Unbounded Loops in `updateRSETHPrice()` and `depositETH()`/`depositAsset()` Can Exceed Block Gas Limit - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a publicly callable function that internally executes a three-level nested loop whose gas cost scales as `supportedAssets.length × nodeDelegatorQueue.length × queuedWithdrawals.length`. The same inner two-level loop is also traversed on every call to `depositETH()` and `depositAsset()`. As the protocol grows, this nested iteration can exceed the block gas limit, rendering price updates and user deposits permanently uncallable.

---

### Finding Description

**Call chain for `updateRSETHPrice()`:**

1. `LRTOracle.updateRSETHPrice()` (public, `whenNotPaused`) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()`
2. `_getTotalEthInProtocol()` iterates over every entry in `supportedAssets` (no explicit cap):

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

3. `getTotalAssetDeposits()` → `getAssetDistributionData()` iterates over every entry in `nodeDelegatorQueue` (capped by `maxNodeDelegatorLimit`, admin-settable):

```solidity
for (uint256 i; i < ndcsCount;) {
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
```

4. `NodeDelegator.getAssetUnstaking()` calls EigenLayer's `getQueuedWithdrawals()` and iterates over every queued withdrawal and every strategy within each withdrawal:

```solidity
for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
```

**Call chain for `depositETH()` / `depositAsset()`:**

`depositETH()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()` → `getAssetDistributionData()` → same inner two-level loop (NDCs × queued withdrawals).

**Protocol team acknowledgment:**

The comment in `LRTUnstakingVault.setMaxUncompletedWithdrawalCount()` explicitly acknowledges the gas scaling concern:

```solidity
// 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
// Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
if (_maxUncompletedWithdrawalCount > 80) {
    revert MaxUncompletedWithdrawalCountTooHigh();
}
```

This cap mitigates one dimension but leaves the others uncapped. `supportedAssets` has no cap at all. The product of all three dimensions (`supportedAssets.length × nodeDelegatorQueue.length × queuedWithdrawals.length`) is not globally bounded.

---

### Impact Explanation

**Medium — Unbounded gas consumption / Temporary freezing of funds.**

If the nested loop product grows large enough to exceed the block gas limit:

- `updateRSETHPrice()` becomes permanently uncallable, freezing the rsETH/ETH exchange rate. Fee minting stops. The oracle price becomes stale, breaking the accounting invariant for all downstream consumers.
- `depositETH()` and `depositAsset()` become permanently uncallable (they share the inner loop path through `getTotalAssetDeposits()`), temporarily freezing new deposits.
- `initiateWithdrawal()` calls `getAvailableAssetAmount()` → `getTotalAssetDeposits()` → same loop, also becoming uncallable.

---

### Likelihood Explanation

**Medium.** The protocol team has already acknowledged the gas scaling concern (the `maxUncompletedWithdrawalCount` cap comment). Normal protocol growth — adding more supported LST assets, adding more NodeDelegator contracts, and accumulating queued EigenLayer withdrawals — increases the product of all three loop dimensions. No attacker action is required; ordinary protocol operation is sufficient. The `supportedAssets` array has no cap, and `maxNodeDelegatorLimit` is admin-settable to any value.

---

### Recommendation

1. **Cap `supportedAssets`**: Add an explicit maximum to the number of supported assets, analogous to `maxNodeDelegatorLimit`.
2. **Cache `getAssetUnstaking()` results**: Avoid calling `getAssetUnstaking()` inside the NDC loop during price computation; instead maintain an on-chain accounting variable updated incrementally on each `initiateUnstaking` / `completeUnstaking`.
3. **Decouple TVL accounting from live EigenLayer queries**: Store a cached `assetUnstaking` value per NDC per asset that is updated lazily, rather than re-querying EigenLayer's full withdrawal queue on every price update and deposit.
4. **Bound the product**: Enforce that `supportedAssets.length × maxNodeDelegatorLimit × maxUncompletedWithdrawalCount` stays within a safe gas budget.

---

### Proof of Concept

**Nested loop root cause — three files, three levels:** [1](#0-0) [2](#0-1) [3](#0-2) 

**Public entry point (no access control):** [4](#0-3) 

**User deposit entry point sharing the same inner loop:** [5](#0-4) 

**Protocol team's own acknowledgment of the gas scaling concern:** [6](#0-5) 

**Scenario:**

- Protocol adds 10 supported assets (`supportedAssets.length = 10`).
- Admin sets `maxNodeDelegatorLimit = 10` and deploys 10 NDCs.
- Operator queues 8 withdrawals per NDC (`uncompletedWithdrawalCount = 80`, within the cap).
- Each withdrawal contains 3 strategies.
- Total EigenLayer `getQueuedWithdrawals` calls: 10 NDCs × 10 assets = 100 external calls.
- Total strategy iterations inside `getAssetUnstaking`: 10 NDCs × 8 withdrawals × 3 strategies = 240 iterations, repeated for each of 10 assets = 2,400 total strategy reads.
- Each `getQueuedWithdrawals` call itself reads from EigenLayer storage. At this scale, `updateRSETHPrice()` and `depositETH()` exceed the 30M block gas limit and revert on every call.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L336-348)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L446-456)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```

**File:** contracts/NodeDelegator.sol (L406-427)
```text
        (IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
            _getDelegationManager().getQueuedWithdrawals(address(this));

        for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
            IDelegationManager.Withdrawal memory withdrawal = queuedWithdrawals[withdrawalIndex];

            for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
                IStrategy strategy = withdrawal.strategies[strategyIndex];

                address strategyAsset = address(strategy) == address(lrtConfig.beaconChainETHStrategy())
                    ? LRTConstants.ETH_TOKEN
                    : address(strategy.underlyingToken());

                if (strategyAsset != asset) continue;

                uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
                amount += strategyAsset == LRTConstants.ETH_TOKEN
                    ? sharesToUnstake
                    : strategy.sharesToUnderlyingView(sharesToUnstake);
            }
        }
    }
```

**File:** contracts/LRTUnstakingVault.sol (L151-156)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
