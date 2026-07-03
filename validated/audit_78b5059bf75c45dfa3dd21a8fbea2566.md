### Title
Missing EigenLayer StrategyManager Approval Causes `depositAssetIntoStrategy` to Always Revert - (File: contracts/NodeDelegator.sol)

### Summary
`NodeDelegator.depositAssetIntoStrategy()` calls EigenLayer's `IStrategyManager.depositIntoStrategy()`, which requires the NodeDelegator to have pre-approved the StrategyManager to spend the asset token. This approval is a separate, manually-triggered step (`maxApproveToEigenStrategyManager`) that is never automatically set during initialization or when new assets are added. If the LRT manager omits this step for any asset on any NodeDelegator, `depositAssetIntoStrategy()` will always revert for that asset, leaving deposited funds stranded in the NDC.

### Finding Description
`NodeDelegator.depositAssetIntoStrategy()` deposits an LST asset held by the NDC into its EigenLayer strategy:

```solidity
// contracts/NodeDelegator.sol
function depositAssetIntoStrategy(address asset) external override nonReentrant whenNotPaused onlySupportedAsset(asset) onlyLRTOperator {
    address strategy = lrtConfig.assetStrategy(asset);
    ...
    IStrategyManager(lrtConfig.strategyManager()).depositIntoStrategy(IStrategy(strategy), token, balance);
}
```

EigenLayer's `StrategyManager.depositIntoStrategy()` explicitly requires the caller to have pre-approved the StrategyManager to pull the token:

> "The `msg.sender` must have previously approved this contract to transfer at least `amount` of `token` on their behalf."

The approval is set via a separate, manually-called function:

```solidity
// contracts/NodeDelegator.sol
function maxApproveToEigenStrategyManager(address asset) external override onlySupportedAsset(asset) onlyLRTManager {
    if (asset == LRTConstants.ETH_TOKEN) { revert ILRTConfig.AssetNotSupported(); }
    IERC20(asset).forceApprove(lrtConfig.strategyManager(), type(uint256).max);
}
```

Neither `initialize()` nor `initialize2()` calls `maxApproveToEigenStrategyManager` for any asset. When a new NodeDelegator is deployed, or when a new supported asset is added via `LRTConfig`, the approval is zero by default. The LRT manager must remember to call `maxApproveToEigenStrategyManager` on every NDC for every asset. If this step is missed, every call to `depositAssetIntoStrategy` for that asset will revert with an ERC-20 insufficient-allowance error.

The same pattern exists in `LRTConverter.transferAssetFromDepositPool()`, which calls `IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount)` and requires `LRTDepositPool.maxApproveToLRTConverter(asset)` to have been called first — also a separate manual step not triggered automatically.

### Impact Explanation
Assets transferred from the deposit pool to a NodeDelegator (via `LRTDepositPool.transferAssetToNodeDelegator`) accumulate in the NDC but cannot be forwarded into EigenLayer strategies. The protocol fails to deliver its core promise of restaking LSTs in EigenLayer. Assets are temporarily frozen in the NDC — they can be recovered via `transferBackToLRTDepositPool`, but only by the Asset Transfer Role, and only after the issue is noticed. This constitutes **temporary freezing of funds** (Medium) and **contract fails to deliver promised returns** (Low).

### Likelihood Explanation
Every new NodeDelegator deployment and every new supported asset addition requires a manual `maxApproveToEigenStrategyManager` call per NDC per asset. With multiple NDCs and multiple assets, the probability of at least one omission is non-trivial. The protocol has no on-chain guard or event that enforces or signals this requirement at deployment time.

### Recommendation
Call `maxApproveToEigenStrategyManager` for all supported assets inside `initialize()` (or `initialize2()`), or add an automatic approval step inside `depositAssetIntoStrategy()` itself (e.g., check allowance and approve if zero). Similarly, `LRTDepositPool.maxApproveToLRTConverter` should be called automatically when a new asset is added to the supported list.

### Proof of Concept
1. Deploy a new `NodeDelegator` and call `initialize(lrtConfigAddr)`.
2. Transfer an LST (e.g., stETH) from the deposit pool to the NDC via `LRTDepositPool.transferAssetToNodeDelegator`.
3. Call `NodeDelegator.depositAssetIntoStrategy(stETH)` as the LRT operator.
4. The call reverts because `IERC20(stETH).allowance(address(ndc), strategyManager) == 0`.
5. Assets are stuck in the NDC until the LRT manager calls `maxApproveToEigenStrategyManager(stETH)` and the operator retries. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/NodeDelegator.sol (L92-112)
```text
    function depositAssetIntoStrategy(address asset)
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlyLRTOperator
    {
        address strategy = lrtConfig.assetStrategy(asset);
        if (strategy == address(0)) {
            revert StrategyIsNotSetForAsset();
        }

        IERC20 token = IERC20(asset);

        uint256 balance = token.balanceOf(address(this));

        IStrategyManager(lrtConfig.strategyManager()).depositIntoStrategy(IStrategy(strategy), token, balance);

        emit AssetDepositIntoStrategy(asset, strategy, balance);
    }
```

**File:** contracts/NodeDelegator.sol (L511-521)
```text
    function maxApproveToEigenStrategyManager(address asset)
        external
        override
        onlySupportedAsset(asset)
        onlyLRTManager
    {
        if (asset == LRTConstants.ETH_TOKEN) {
            revert ILRTConfig.AssetNotSupported();
        }
        IERC20(asset).forceApprove(lrtConfig.strategyManager(), type(uint256).max);
    }
```

**File:** contracts/LRTConverter.sol (L128-143)
```text
    function transferAssetFromDepositPool(
        address _asset,
        uint256 _amount
    )
        external
        onlySupportedERC20Token(_asset)
        onlyAssetTransferRole
    {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L362-368)
```text
    /// @notice Approves the maximum amount of an asset to the LRTConverter contract
    /// @dev only supported assets can be deposited and only called by the LRT manager
    /// @param asset the asset to approve
    function maxApproveToLRTConverter(address asset) external onlySupportedERC20Token(asset) onlyLRTManager {
        address lrtConverterAddress = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        IERC20(asset).forceApprove(lrtConverterAddress, type(uint256).max);
    }
```
