### Title
Unbounded Gas Consumption in Public `updateRSETHPrice()` Due to Nested Iteration Over Assets, NDCs, and EigenLayer Queued Withdrawals - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is publicly callable with no access control beyond `whenNotPaused`. Its gas cost grows proportionally with the number of supported assets, the number of NodeDelegator contracts (NDCs), and the number of queued EigenLayer withdrawals per NDC. As the protocol scales, this function can become prohibitively expensive or exceed the block gas limit, making price updates impossible and freezing protocol fee accrual.

### Finding Description
`updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction. It internally calls `_getTotalEthInProtocol()`, which iterates over every supported asset and for each asset calls `ILRTDepositPool.getTotalAssetDeposits(asset)`. That function calls `getAssetDistributionData(asset)`, which loops over every NDC in `nodeDelegatorQueue` and calls `INodeDelegator.getAssetUnstaking(asset)` on each one. `getAssetUnstaking` in turn calls `_getDelegationManager().getQueuedWithdrawals(address(this))` — fetching the full list of pending EigenLayer withdrawals — and then performs a **nested loop** over every withdrawal and every strategy within each withdrawal.

The full call chain is:

```
updateRSETHPrice()                          [public, no role guard]
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()
            └─ for each supported asset:
                 getTotalAssetDeposits(asset)
                   └─ getAssetDistributionData(asset)
                        └─ for each NDC in nodeDelegatorQueue:
                             getAssetUnstaking(asset)
                               └─ getQueuedWithdrawals(this)   [EigenLayer external call]
                                    └─ for each withdrawal:
                                         for each strategy:
                                              sharesToUnderlyingView(...)
```

Effective gas cost is **O(assets × NDCs × queued_withdrawals × strategies_per_withdrawal)**. All four dimensions grow as the protocol operates normally: assets are added by governance, NDCs are added by admin (up to `maxNodeDelegatorLimit`, which is itself admin-configurable), and queued withdrawals accumulate with every `initiateUnstaking` or `undelegate` call. There is no hard ceiling that prevents the aggregate from exceeding the block gas limit.

The same nested computation is also triggered on every user deposit via `depositETH`/`depositAsset` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits`.

### Impact Explanation
If `updateRSETHPrice()` (and `updateRSETHPriceAsManager()`, which calls the same `_updateRsETHPrice()`) exceeds the block gas limit:

1. **Protocol fee minting is permanently blocked** — `_updateRsETHPrice()` is the only path that mints rsETH as protocol fees; if it reverts, yield accrual stops entirely (High: theft of unclaimed yield).
2. **Price protection mechanisms fail** — the downside-protection auto-pause and the upside price-cap check both live inside `_updateRsETHPrice()`; neither can fire.
3. **User deposits become uncallable** — `_beforeDeposit` calls `getTotalAssetDeposits`, which executes the same nested loop; if gas exceeds the block limit, all deposits revert (Medium: temporary freezing of funds).

### Likelihood Explanation
The protocol is designed to grow: more LST assets are added over time, more NDCs are deployed to scale restaking capacity, and queued withdrawals accumulate continuously during normal operation. No single parameter caps the combined product. The `maxNodeDelegatorLimit` (default 10, line 49 of `LRTDepositPool.sol`) and `maxUncompletedWithdrawalCount` are both admin-configurable with no on-chain upper bound, so a governance decision to scale the protocol can inadvertently push gas past the block limit. Because `updateRSETHPrice()` is public, any external caller can trigger the expensive computation at any time, amplifying the DoS surface.

### Recommendation
1. **Restrict `updateRSETHPrice()` to a privileged role** (e.g., `onlyLRTManager` or a dedicated keeper role), analogous to the external report's recommendation to move the API to a non-default namespace. This prevents arbitrary callers from triggering the expensive computation.
2. **Cache or snapshot asset totals** rather than recomputing them on every call. Store a running `totalAssetDeposits` per asset that is updated incrementally on deposit/withdrawal events.
3. **Remove `getAssetUnstaking` from the hot path** used by `getTotalAssetDeposits`. Track unstaking amounts in a storage variable updated at `initiateUnstaking`/`completeUnstaking` time instead of re-querying EigenLayer on every price update.
4. **Enforce a hard cap** on `maxNodeDelegatorLimit` and `maxUncompletedWithdrawalCount` that is validated against a gas budget estimate before being accepted.

### Proof of Concept

**Entry point — no access control:** [1](#0-0) 

**Outer loop — iterates every supported asset:** [2](#0-1) 

**Middle loop — iterates every NDC, calls `getAssetUnstaking` per NDC:** [3](#0-2) 

**Inner nested loop — fetches all EigenLayer queued withdrawals and iterates strategies:** [4](#0-3) 

**Same nested computation triggered on every user deposit:** [5](#0-4) 

**`maxNodeDelegatorLimit` is admin-configurable with no hard ceiling:** [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L333-348)
```text
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

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

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

**File:** contracts/NodeDelegator.sol (L405-427)
```text
    function getAssetUnstaking(address asset) external view returns (uint256 amount) {
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
