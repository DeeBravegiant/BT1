### Title
Unbounded Nested Loop in `NodeDelegator.getAssetUnstaking()` Propagates to User-Facing Deposit and Withdrawal Entry Points — (File: contracts/NodeDelegator.sol)

---

### Summary

`NodeDelegator.getAssetUnstaking()` performs an unbounded nested iteration over all EigenLayer queued withdrawals. This function is called transitively from `LRTDepositPool.getAssetDistributionData()`, which is itself called from every user-facing deposit and withdrawal entry point. As the protocol accumulates more NodeDelegator contracts (NDCs) and queued EigenLayer withdrawals through normal operation, the gas cost of `depositETH()`, `depositAsset()`, `initiateWithdrawal()`, and the public `updateRSETHPrice()` grows without a hard ceiling, eventually making these functions uncallable.

---

### Finding Description

`NodeDelegator.getAssetUnstaking()` fetches the full list of pending EigenLayer withdrawals via `getQueuedWithdrawals()` and iterates over them with a nested loop: [1](#0-0) 

This function is called inside `LRTDepositPool.getAssetDistributionData()`, once per NDC in `nodeDelegatorQueue`: [2](#0-1) 

`getAssetDistributionData()` is called by `getTotalAssetDeposits()`, which is called by `_checkIfDepositAmountExceedesCurrentLimit()` inside every deposit: [3](#0-2) 

The same `getTotalAssetDeposits()` is called inside `LRTOracle._getTotalEthInProtocol()`, which loops over every supported asset: [4](#0-3) 

`_getTotalEthInProtocol()` is invoked by the public, permissionless `updateRSETHPrice()`: [5](#0-4) 

`LRTWithdrawalManager.initiateWithdrawal()` calls `getAvailableAssetAmount()` → `getTotalAssetDeposits()`, hitting the same path: [6](#0-5) 

The effective gas complexity is:

```
O(supportedAssets × NDCs × queuedWithdrawals × strategiesPerWithdrawal)
```

Each dimension grows through normal protocol operation:
- `supportedAssets` grows via `addNewSupportedAsset()` (admin-gated, but routine).
- `nodeDelegatorQueue` grows up to `maxNodeDelegatorLimit` (default 10, admin-adjustable upward).
- `queuedWithdrawals` per NDC grows with each `initiateUnstaking()` call (operator-gated, routine).
- Each withdrawal can contain multiple strategies.

There is no gas cap or pagination on any of these loops.

---

### Impact Explanation

When the cumulative gas cost of the nested iteration exceeds the block gas limit (~30M gas on Ethereum mainnet), every call to `depositETH()`, `depositAsset()`, `initiateWithdrawal()`, and `updateRSETHPrice()` reverts with out-of-gas. This permanently freezes user deposits and withdrawals — funds already in the protocol cannot be withdrawn via the normal path, and no new deposits can be accepted. This constitutes **temporary-to-permanent freezing of funds**.

---

### Likelihood Explanation

The protocol is designed to scale: multiple NDCs are expected, each accumulating queued EigenLayer withdrawals over time. With 10 NDCs (the default `maxNodeDelegatorLimit`), 5 supported assets, and 20 queued withdrawals per NDC each containing 3 strategies, `_getTotalEthInProtocol()` alone triggers 5 × 10 = 50 calls to `getAssetUnstaking()`, each making a cold external call to EigenLayer and iterating over 20 × 3 = 60 entries. This is already in the millions-of-gas range. The likelihood increases as the protocol matures and `maxNodeDelegatorLimit` is raised.

---

### Recommendation

1. **Cache `getQueuedWithdrawals()` results** or replace the per-call iteration with an operator-maintained accounting variable that is updated incrementally on `initiateUnstaking()` and `completeUnstaking()`.
2. **Paginate or bound** the NDC loop in `getAssetDistributionData()` and `getETHDistributionData()`.
3. **Decouple the oracle price update** from the full TVL recomputation: store a pre-computed TVL that operators update off-chain and submit on-chain, rather than recomputing it inline on every call.
4. **Add a hard cap** on `maxNodeDelegatorLimit` and `maxUncompletedWithdrawalCount` that accounts for the block gas limit.

---

### Proof of Concept

1. Protocol has 5 supported assets, 10 NDCs, each NDC has 20 queued EigenLayer withdrawals with 3 strategies each.
2. Any user calls `depositETH(1 ether, 0, "")`.
3. Execution path: `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits` → `getAssetDistributionData` → 10 iterations, each calling `getAssetUnstaking` → `getQueuedWithdrawals` (external) + 20×3 inner iterations.
4. This repeats for each of the 5 assets in `_getTotalEthInProtocol()` if `updateRSETHPrice()` is called.
5. Gas consumption grows proportionally; at sufficient scale the transaction reverts with out-of-gas, permanently blocking deposits and withdrawals. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L168-170)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```
