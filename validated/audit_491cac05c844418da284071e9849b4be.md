### Title
Missing Approval Migration When `strategyManager` Is Updated in `LRTConfig` Permanently Breaks `depositAssetIntoStrategy` - (File: `contracts/NodeDelegator.sol`)

---

### Summary

`NodeDelegator.maxApproveToEigenStrategyManager` grants an unlimited ERC-20 allowance to whichever address `lrtConfig.strategyManager()` resolves to at call time. When `LRTConfig.setContract` later replaces the `EIGEN_STRATEGY_MANAGER` address, every `NodeDelegator` retains its max allowance to the **old** strategy manager while the **new** strategy manager receives zero allowance. Because `depositAssetIntoStrategy` calls `IStrategyManager(lrtConfig.strategyManager()).depositIntoStrategy(...)` using the **new** address, the EigenLayer pull-transfer fails for every LST asset across every NDC. Compounding the problem, `revokeApprovalToEigenStrategyManager` also resolves the address dynamically from `lrtConfig`, so after the update it revokes allowance from the new (zero-allowance) address rather than the old (max-allowance) one, making the stale approval irrevocable through the contract's own interface.

---

### Finding Description

`NodeDelegator.maxApproveToEigenStrategyManager` reads the strategy manager address at call time and issues a `forceApprove(..., type(uint256).max)`: [1](#0-0) 

`depositAssetIntoStrategy` also resolves the address dynamically: [2](#0-1) 

`revokeApprovalToEigenStrategyManager` likewise resolves the address dynamically: [3](#0-2) 

`LRTConfig.setContract` can replace the `EIGEN_STRATEGY_MANAGER` entry at any time: [4](#0-3) 

After the replacement:

1. `depositAssetIntoStrategy` calls `IStrategyManager(newAddress).depositIntoStrategy(...)`. The new strategy manager attempts to `transferFrom` the NDC, but the NDC's allowance for `newAddress` is 0 → the call reverts for every LST asset on every NDC.
2. `revokeApprovalToEigenStrategyManager` now targets `newAddress` (allowance already 0), leaving `oldAddress` permanently at `type(uint256).max`. There is no other code path in `NodeDelegator` that can zero out the old allowance.

---

### Impact Explanation

All LST assets sitting in `NodeDelegator` contracts cannot be deposited into EigenLayer strategies until the manager manually calls `maxApproveToEigenStrategyManager` for every asset on every NDC. During this window, assets are frozen inside the NDCs and cannot accrue EigenLayer restaking yield. Additionally, the old strategy manager retains an irrevocable unlimited allowance over every NDC's LST holdings; if that address is ever exploited or re-deployed maliciously, it can drain all LST balances from every NDC.

Primary scoped impact: **Temporary freezing of funds (Medium)**.
Secondary scoped impact: **Potential direct theft of user funds (Critical)** if the old strategy manager address is later compromised or reused maliciously.

---

### Likelihood Explanation

`LRTConfig.setContract` is callable by `DEFAULT_ADMIN_ROLE` and is the standard upgrade path for swapping out EigenLayer integration contracts (e.g., during an EigenLayer upgrade). The admin has no on-chain prompt to re-approve NDCs after the swap, making the omission easy to miss. The likelihood of the strategy manager address being updated at least once over the protocol's lifetime is moderate; the likelihood of the stale approval being exploited depends on what happens to the old address.

---

### Recommendation

1. In `LRTConfig.setContract`, when the key is `EIGEN_STRATEGY_MANAGER`, emit a dedicated event or revert with a migration checklist requiring callers to re-approve all NDCs.
2. Add a `revokeApprovalToAddress(address asset, address spender)` function in `NodeDelegator` (restricted to `onlyLRTManager`) so the old strategy manager's allowance can be explicitly zeroed after a migration.
3. Alternatively, mirror the pattern used in `KernelReceiver.setStakerGateway`, which atomically revokes the old approval and grants the new one in the same transaction: [5](#0-4) 

---

### Proof of Concept

1. Admin calls `LRTConfig.setContract(EIGEN_STRATEGY_MANAGER_KEY, newStrategyManager)`.
2. Operator calls `NodeDelegator.depositAssetIntoStrategy(stETH)`.
   - Internally: `IStrategyManager(newStrategyManager).depositIntoStrategy(...)` is called.
   - `newStrategyManager` attempts `stETH.transferFrom(ndc, newStrategyManager, balance)`.
   - NDC's allowance for `newStrategyManager` is 0 → ERC-20 reverts → entire call reverts.
3. Operator calls `NodeDelegator.revokeApprovalToEigenStrategyManager(stETH)` hoping to clean up.
   - Internally: `stETH.forceApprove(newStrategyManager, 0)` — targets the wrong address.
   - `oldStrategyManager` retains `type(uint256).max` allowance permanently.
4. Until the manager calls `maxApproveToEigenStrategyManager(stETH)` on every NDC, all LST deposits into EigenLayer are frozen.

### Citations

**File:** contracts/NodeDelegator.sol (L100-111)
```text
        address strategy = lrtConfig.assetStrategy(asset);
        if (strategy == address(0)) {
            revert StrategyIsNotSetForAsset();
        }

        IERC20 token = IERC20(asset);

        uint256 balance = token.balanceOf(address(this));

        IStrategyManager(lrtConfig.strategyManager()).depositIntoStrategy(IStrategy(strategy), token, balance);

        emit AssetDepositIntoStrategy(asset, strategy, balance);
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

**File:** contracts/NodeDelegator.sol (L526-528)
```text
    function revokeApprovalToEigenStrategyManager(address asset) external override onlyLRTManager {
        IERC20(asset).forceApprove(lrtConfig.strategyManager(), 0);
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

**File:** contracts/KERNEL/KernelReceiver.sol (L170-185)
```text
    function setStakerGateway(address _stakerGateway) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(_stakerGateway);

        IStakerGateway oldStakerGateway = stakerGateway;
        stakerGateway = IStakerGateway(_stakerGateway);

        // Revoke the approval of the old StakerGateway contract to spend KERNEL tokens on behalf of this contract
        kernel.forceApprove(address(oldStakerGateway), 0);

        // Approve the new StakerGateway contract to spend an unlimited amount of KERNEL tokens on behalf of this
        // contract in order to avoid the need to approve the contract every time an operator stakes KERNEL tokens on
        // behalf of a user
        kernel.forceApprove(_stakerGateway, type(uint256).max);

        emit StakerGatewayUpdated(_stakerGateway, address(oldStakerGateway));
    }
```
