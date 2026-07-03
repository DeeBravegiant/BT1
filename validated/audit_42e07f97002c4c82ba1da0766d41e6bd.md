### Title
Unbounded `supportedAssetList` Iteration in `updateRSETHPrice` Can Permanently Freeze Protocol Price Updates — (`contracts/LRTOracle.sol`)

### Summary
`LRTOracle._getTotalEthInProtocol()` iterates over `lrtConfig.getSupportedAssetList()` with no on-chain upper-bound check on the list's length. Because `LRTConfig._addNewSupportedAsset()` imposes no maximum size on `supportedAssetList`, and because `updateRSETHPrice()` is a public function, the gas cost of a price update grows linearly (and in practice super-linearly due to nested node-delegator loops) with the number of supported assets. If the list grows large enough, every call to `updateRSETHPrice()` will exceed the block gas limit, permanently freezing the rsETH price and bricking deposits, withdrawals, and the oracle-dependent fee mechanism.

### Finding Description

`LRTConfig._addNewSupportedAsset()` pushes to `supportedAssetList` with no cap: [1](#0-0) 

`LRTOracle._getTotalEthInProtocol()` then iterates over the entire list: [2](#0-1) 

Inside each iteration, `ILRTDepositPool.getTotalAssetDeposits(asset)` is called. That function itself iterates over `nodeDelegatorQueue` (up to `maxNodeDelegatorLimit = 10`) and, for each node delegator, calls `INodeDelegator.getAssetUnstaking()`: [3](#0-2) 

`getAssetUnstaking()` in turn calls EigenLayer's `getQueuedWithdrawals()` and iterates over all queued withdrawals with a nested loop: [4](#0-3) 

The effective gas cost per `updateRSETHPrice()` call is therefore `O(supportedAssets × nodeDelegators × queuedWithdrawals)`. There is no on-chain guard preventing `supportedAssetList` from growing to a size that makes this product exceed the block gas limit.

The public entry point is: [5](#0-4) 

### Impact Explanation

`rsETHPrice` is the single source of truth for minting rsETH in `LRTDepositPool`, for computing withdrawal amounts in `LRTWithdrawalManager`, and for the protocol fee mechanism in `LRTOracle`. If `updateRSETHPrice()` permanently reverts due to an out-of-gas condition, the price becomes stale and the protocol is effectively frozen: no new deposits can be correctly priced, no withdrawals can be unlocked at a fair rate, and the fee-minting path is broken. This constitutes **permanent freezing of funds** (Critical) or at minimum **unbounded gas consumption** (Medium) depending on how stale-price-dependent the downstream functions are.

### Likelihood Explanation

`addNewSupportedAsset` is gated by `TIME_LOCK_ROLE`, so the list grows only through legitimate governance. However, the protocol is designed to support multiple LSTs and the list has already grown since launch. There is no on-chain maximum, so a governance decision to add a moderate number of additional assets (e.g., 20–30) combined with a large EigenLayer withdrawal queue could push the per-call gas cost past the block limit. This is an accidental, non-adversarial path — exactly the scenario the original report warns about.

### Recommendation

1. Add a maximum size check in `LRTConfig._addNewSupportedAsset()`:
   ```solidity
   uint256 public constant MAX_SUPPORTED_ASSETS = 20;
   require(supportedAssetList.length < MAX_SUPPORTED_ASSETS, "Too many assets");
   ```
2. Consider caching or snapshotting the per-asset TVL off-chain and providing it as a calldata argument to `updateRSETHPrice()`, with on-chain bounds validation, rather than computing it entirely on-chain in a single transaction.
3. Add a similar cap to `maxNodeDelegatorLimit` and document the combined gas budget.

### Proof of Concept

1. Governance (via `TIME_LOCK_ROLE`) calls `LRTConfig.addNewSupportedAsset()` repeatedly, adding N assets.
2. Operators queue many EigenLayer withdrawals via `NodeDelegator.queueWithdrawals()`, growing the `getQueuedWithdrawals()` return array.
3. Any external caller invokes `LRTOracle.updateRSETHPrice()`.
4. The call enters `_getTotalEthInProtocol()`, which loops N times; each iteration calls `getTotalAssetDeposits()`, which loops over up to 10 node delegators; each node delegator call invokes `getAssetUnstaking()`, which loops over all queued EigenLayer withdrawals.
5. At sufficient N × delegators × queued-withdrawals, the transaction reverts with out-of-gas, and no future call can succeed — the price is permanently frozen. [5](#0-4) [2](#0-1) [1](#0-0)

### Citations

**File:** contracts/LRTConfig.sol (L106-117)
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

**File:** contracts/LRTDepositPool.sol (L631-645)
```text
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
```

**File:** contracts/NodeDelegator.sol (L409-426)
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
```
