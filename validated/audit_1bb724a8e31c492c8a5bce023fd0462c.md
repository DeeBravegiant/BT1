Audit Report

## Title
Unbounded Gas Consumption in `updateRSETHPrice()` Due to Uncapped `supportedAssetList` — (File: contracts/LRTOracle.sol)

## Summary

`updateRSETHPrice()` is publicly callable with no access control and iterates over the entire `supportedAssetList` with no enforced cap. For each asset it makes multiple external calls including a nested loop over all node delegators, each of which iterates over all queued EigenLayer withdrawals. Because `supportedAssetList` has no maximum size, gas cost grows without bound as the protocol adds more supported assets, eventually exceeding the block gas limit and permanently freezing the rsETH price oracle.

## Finding Description

`updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction — any external caller can invoke it. [1](#0-0) 

It delegates to `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`. [2](#0-1) 

`_getTotalEthInProtocol()` fetches the full `supportedAssetList` from `LRTConfig` and iterates over every entry with no cap, making two expensive external calls per asset: `getAssetPrice(asset)` (external oracle) and `ILRTDepositPool.getTotalAssetDeposits(asset)`. [3](#0-2) 

`getTotalAssetDeposits` calls `getAssetDistributionData`, which loops over the entire `nodeDelegatorQueue` and for each NDC calls `getAssetBalance` and `getAssetUnstaking`. [4](#0-3) 

`getAssetUnstaking` on each `NodeDelegator` fetches all queued EigenLayer withdrawals via `getQueuedWithdrawals` and iterates over their strategies. [5](#0-4) 

The root cause is that `_addNewSupportedAsset()` in `LRTConfig` enforces no cap on `supportedAssetList`. [6](#0-5) 

While `nodeDelegatorQueue` is capped by `maxNodeDelegatorLimit` (initialized to 10) and queued withdrawals are globally capped by `maxUncompletedWithdrawalCount` in `LRTUnstakingVault`, the `supportedAssetList` dimension is entirely uncapped. [7](#0-6) [8](#0-7) 

The total gas cost is therefore **O(supportedAssets × nodeDelegators × queuedWithdrawals)**, with the `supportedAssets` dimension having no ceiling.

## Impact Explanation

If `supportedAssetList` grows large enough, `updateRSETHPrice()` will exceed the block gas limit and become permanently uncallable. This freezes `rsETHPrice`. Downstream effects include: `depositETH`/`depositAsset` computing `rsethAmountToMint` using a stale price, `initiateWithdrawal` computing incorrect `expectedAssetAmount`, and the price-drop pause protection becoming permanently disabled. This matches the allowed impact: **Medium — Unbounded gas consumption**.

## Likelihood Explanation

`TIME_LOCK_ROLE` can add new supported assets via `addNewSupportedAsset` with no cap. As the protocol expands to support more LSTs, the list grows organically through normal protocol operation — no adversarial action is required. The trigger (`updateRSETHPrice()`) is callable by any unprivileged address at any time. Likelihood is **Low-Medium**: requires organic protocol growth but no attacker capability beyond calling a public function.

## Recommendation

Enforce a maximum cap on `supportedAssetList` inside `_addNewSupportedAsset()` in `LRTConfig.sol`, analogous to how `maxNodeDelegatorLimit` caps `nodeDelegatorQueue` in `LRTDepositPool`:

```solidity
uint256 public maxSupportedAssets; // e.g., initialized to 20

function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
    if (supportedAssetList.length >= maxSupportedAssets) {
        revert MaxSupportedAssetsReached();
    }
    // ... existing logic
}
```

## Proof of Concept

1. Protocol adds N supported assets via `TIME_LOCK_ROLE` (e.g., N = 50 LSTs).
2. Each asset has a corresponding node delegator with queued EigenLayer withdrawals.
3. Any unprivileged address calls `updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` iterates 50 times; each iteration calls `getAssetPrice` (external oracle) + `getTotalAssetDeposits` → `getAssetDistributionData` → loops over all NDCs → each NDC calls `getAssetUnstaking` → loops over all queued withdrawals.
5. Total gas exceeds the 30M block gas limit; `updateRSETHPrice()` reverts on every call.
6. `rsETHPrice` is permanently frozen; all subsequent deposits and withdrawals use the last stored stale price.

A Foundry fork test can demonstrate this by deploying N mock LST assets with mock oracles, registering them via `addNewSupportedAsset`, adding NDCs with queued withdrawals, then calling `updateRSETHPrice()` with `vm.expectRevert()` after gas measurement confirms block limit breach.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-231)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
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
    }
```

**File:** contracts/LRTDepositPool.sol (L49-49)
```text
        maxNodeDelegatorLimit = 10;
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

**File:** contracts/LRTUnstakingVault.sol (L39-40)
```text
    uint256 public uncompletedWithdrawalCount;
    uint256 public maxUncompletedWithdrawalCount;
```
