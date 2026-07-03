### Title
Unbounded Nested Loop Gas Consumption in `updateRSETHPrice()` via `_getTotalEthInProtocol()` - (File: contracts/LRTOracle.sol, contracts/LRTDepositPool.sol, contracts/NodeDelegator.sol)

---

### Summary
The public `updateRSETHPrice()` function in `LRTOracle` triggers a deeply nested loop chain across `supportedAssets`, `nodeDelegatorQueue`, and EigenLayer queued withdrawals. The `supportedAssets` array has no maximum length cap. As the protocol grows, this chain can consume unbounded gas, causing `updateRSETHPrice()` to revert and permanently stale the rsETH price.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is a public function callable by anyone. It calls `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`. That function iterates over every entry in `supportedAssets` (fetched from `lrtConfig.getSupportedAssetList()`):

```solidity
// LRTOracle.sol line 336
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    ...
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

`getTotalAssetDeposits()` calls `getAssetDistributionData()`, which itself iterates over `nodeDelegatorQueue`:

```solidity
// LRTDepositPool.sol line 447
for (uint256 i; i < ndcsCount;) {
    ...
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
```

`getAssetUnstaking()` in `NodeDelegator` then iterates over all queued EigenLayer withdrawals and their strategies:

```solidity
// NodeDelegator.sol line 409
for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    ...
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
```

The `supportedAssets` array in `LRTConfig` has **no maximum length check** in `_addNewSupportedAsset()`. The `maxNodeDelegatorLimit` has no upper bound in `updateMaxNodeDelegatorLimit()`. The `maxUncompletedWithdrawalCount` is capped at 80. The total iteration count is `supportedAssets.length × nodeDelegatorQueue.length × queuedWithdrawals.length × strategies.length`, all of which can grow over time.

---

### Impact Explanation

If `updateRSETHPrice()` runs out of gas, the rsETH price stored in `rsETHPrice` becomes permanently stale. The protocol's price-deviation circuit breaker (which pauses deposits and withdrawals when price moves too far) cannot trigger. Deposits and withdrawals continue using a stale price, leading to incorrect rsETH minting/burning ratios. The protocol cannot update its NAV, which is a core invariant. This matches **Medium — Unbounded gas consumption**.

---

### Likelihood Explanation

The protocol is designed to support multiple LST assets and multiple NodeDelegators. As the protocol scales (more supported assets, more NDCs, more EigenLayer queued withdrawals), the gas cost of `updateRSETHPrice()` grows multiplicatively. No privileged action is needed to trigger the failure — any caller of the public `updateRSETHPrice()` will encounter the revert once the arrays are large enough. The `supportedAssets` array has no cap, and `maxNodeDelegatorLimit` has no upper bound.

---

### Recommendation

1. Add a maximum length cap on `supportedAssetList` in `LRTConfig._addNewSupportedAsset()`.
2. Add an upper bound check in `LRTDepositPool.updateMaxNodeDelegatorLimit()`.
3. Refactor `_getTotalEthInProtocol()` to avoid nested external calls per asset per NDC per withdrawal — consider caching intermediate values or splitting the computation across multiple transactions.

---

### Proof of Concept

Call chain from a public entry point:

1. Anyone calls `LRTOracle.updateRSETHPrice()` [1](#0-0) 

2. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which loops over `supportedAssets` with no max-length guard: [2](#0-1) 

3. Each iteration calls `getTotalAssetDeposits()` → `getAssetDistributionData()`, which loops over `nodeDelegatorQueue` (no upper bound on `maxNodeDelegatorLimit`): [3](#0-2) 

4. Each NDC call to `getAssetUnstaking()` loops over all EigenLayer queued withdrawals and their strategies: [4](#0-3) 

5. `supportedAssets` has no maximum length cap: [5](#0-4) 

6. `maxNodeDelegatorLimit` has no upper bound: [6](#0-5) 

With `N` supported assets × `M` NDCs × `K` queued withdrawals × `S` strategies per withdrawal, gas grows as O(N × M × K × S). At realistic protocol scale (e.g., 10 assets × 10 NDCs × 80 withdrawals), the function will exceed the block gas limit, permanently preventing rsETH price updates.

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

**File:** contracts/LRTDepositPool.sol (L290-297)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
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

**File:** contracts/NodeDelegator.sol (L409-427)
```text
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

**File:** contracts/LRTConfig.sol (L106-118)
```text
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
    }
```
