### Title
Stale EigenLayer Strategy Manager Approval Breaks `depositAssetIntoStrategy` After Config Update - (File: contracts/NodeDelegator.sol)

### Summary
`NodeDelegator.maxApproveToEigenStrategyManager` grants a max ERC-20 allowance to whichever address `lrtConfig.strategyManager()` resolves to at the moment of the call. Because `LRTConfig.setContract` lets the admin replace `EIGEN_STRATEGY_MANAGER` at any time, a routine config update silently invalidates every existing approval. After the update, `depositAssetIntoStrategy` always reverts because the new strategy manager has no allowance to pull tokens from the NodeDelegator.

### Finding Description
`NodeDelegator.maxApproveToEigenStrategyManager` reads the strategy manager address from `LRTConfig` and issues a `forceApprove(..., type(uint256).max)` to that address: [1](#0-0) 

`depositAssetIntoStrategy` also reads the strategy manager from `LRTConfig` at call time and invokes `depositIntoStrategy`, which internally calls `transferFrom(nodeDelegator, strategy, balance)`: [2](#0-1) 

The strategy manager address is stored in `LRTConfig.contractMap` under the key `EIGEN_STRATEGY_MANAGER`: [3](#0-2) 

`LRTConfig.setContract` allows `DEFAULT_ADMIN_ROLE` to replace any entry in `contractMap`, including `EIGEN_STRATEGY_MANAGER`, with no side-effect on existing approvals: [4](#0-3) 

After the replacement, `depositAssetIntoStrategy` resolves the new strategy manager address but the NodeDelegator's ERC-20 allowance still points to the old address. The new strategy manager's `transferFrom` call reverts with an insufficient-allowance error, permanently blocking asset deposits into EigenLayer until a manager manually re-approves every (asset, NodeDelegator) pair.

The same structural issue exists for `LRTDepositPool.maxApproveToLRTConverter` / `LRT_CONVERTER`: [5](#0-4) 

### Impact Explanation
After a legitimate `setContract(EIGEN_STRATEGY_MANAGER, newAddr)` call, every subsequent call to `depositAssetIntoStrategy` on every NodeDelegator reverts. LST assets accumulate in the NodeDelegator contracts and cannot be restaked into EigenLayer, halting yield generation and blocking the core restaking flow. This constitutes **temporary freezing of funds** (Medium).

### Likelihood Explanation
EigenLayer's `StrategyManager` is a proxy contract that can be upgraded or replaced. Kelp DAO may need to point to a new address after an EigenLayer upgrade or migration. The admin role is a multisig, not a single key, so the change is a realistic operational event. The re-approval step (`maxApproveToEigenStrategyManager`) is a separate, manually triggered transaction that is easy to overlook when updating the config, especially across multiple NodeDelegator instances and multiple supported assets.

### Recommendation
Add an `afterSet` hook or an event listener pattern so that whenever `EIGEN_STRATEGY_MANAGER` is updated in `LRTConfig`, all NodeDelegators automatically revoke the old allowance and grant the new one. Alternatively, have `depositAssetIntoStrategy` verify that the current allowance to `lrtConfig.strategyManager()` is non-zero before proceeding, and revert with a descriptive error prompting the manager to call `maxApproveToEigenStrategyManager` first.

### Proof of Concept
1. Admin calls `LRTConfig.setContract(EIGEN_STRATEGY_MANAGER, newStrategyManager)`.
2. `newStrategyManager` has zero allowance from every NodeDelegator for every supported LST.
3. LRT operator calls `NodeDelegator.depositAssetIntoStrategy(stETH)`.
4. Internally: `IStrategyManager(newStrategyManager).depositIntoStrategy(strategy, stETH, balance)` is called.
5. `newStrategyManager` attempts `stETH.transferFrom(nodeDelegator, strategy, balance)`.
6. `stETH.allowance(nodeDelegator, newStrategyManager) == 0` → revert.
7. All LST assets remain stranded in the NodeDelegator; no restaking is possible until `maxApproveToEigenStrategyManager` is called for each (asset, NDC) pair — a step with no on-chain enforcement. [6](#0-5) [1](#0-0) [4](#0-3)

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

**File:** contracts/utils/LRTConstants.sol (L57-59)
```text
    function strategyManager(ILRTConfig config) internal view returns (address) {
        return config.getContract(EIGEN_STRATEGY_MANAGER);
    }
```

**File:** contracts/LRTConfig.sol (L237-251)
```text
    function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setContract(contractKey, contractAddress);
    }

    /// @dev private function to set a contract
    /// @param key Contract key
    /// @param val Contract address
    function _setContract(bytes32 key, address val) private {
        UtilLib.checkNonZeroAddress(val);
        if (contractMap[key] == val) {
            revert ValueAlreadyInUse();
        }
        contractMap[key] = val;
        emit SetContract(key, val);
    }
```

**File:** contracts/LRTDepositPool.sol (L365-368)
```text
    function maxApproveToLRTConverter(address asset) external onlySupportedERC20Token(asset) onlyLRTManager {
        address lrtConverterAddress = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        IERC20(asset).forceApprove(lrtConverterAddress, type(uint256).max);
    }
```
