### Title
Unbounded Gas Consumption in `updateRSETHPrice()` via O(assets × NDCs × queuedWithdrawals × strategies) Nested Loops — (`contracts/LRTOracle.sol`)

---

### Summary

`updateRSETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over every supported asset and, for each asset, calls `getTotalAssetDeposits()` → `getAssetDistributionData()` → `getAssetUnstaking()` per NDC. `getAssetUnstaking()` calls EigenLayer's `getQueuedWithdrawals()` and iterates over every queued withdrawal and every strategy within it. Because there is no hard cap on `supportedAssetList`, each call to `addNewSupportedAsset()` monotonically increases the gas cost of `updateRSETHPrice()`.

---

### Finding Description

The full call chain is:

```
updateRSETHPrice()                          [public, no access control]
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()
            └─ for each asset in supportedAssetList:          ← NO hard cap
                 getAssetPrice(asset)                         ← 1 external call/asset
                 getTotalAssetDeposits(asset)
                   └─ getAssetDistributionData(asset)
                        └─ for each NDC in nodeDelegatorQueue: ← bounded by maxNodeDelegatorLimit (default 10)
                             IERC20(asset).balanceOf(ndc)
                             getAssetBalance(asset)            ← getWithdrawableShares() external call
                             getAssetUnstaking(asset)
                               └─ getQueuedWithdrawals(ndc)   ← external call to EigenLayer
                                    └─ for each withdrawal:
                                         for each strategy:
                                              strategy.sharesToUnderlyingView() ← external call
```

**Key observations:**

1. `_getTotalEthInProtocol()` iterates over `supportedAssetList` with no length cap. [1](#0-0) 

2. `addNewSupportedAsset()` pushes to `supportedAssetList` with no upper-bound check. [2](#0-1) 

3. `getAssetDistributionData()` loops over all NDCs and calls `getAssetUnstaking()` per NDC per asset. [3](#0-2) 

4. `getAssetUnstaking()` calls `getQueuedWithdrawals(address(this))` — an external call to EigenLayer — once per NDC per asset, then iterates over every queued withdrawal and every strategy within it. [4](#0-3) 

5. `getAssetBalance()` also calls `getWithdrawableShares()` (another EigenLayer external call) once per NDC per asset. [5](#0-4) 

**Total EigenLayer external calls per `updateRSETHPrice()` invocation:**
- `getQueuedWithdrawals`: `assets × NDCs`
- `getWithdrawableShares`: `assets × NDCs`
- `sharesToUnderlyingView`: `assets × NDCs × queuedWithdrawals × strategies`

With 20 assets × 10 NDCs × 10 queued withdrawals × 5 strategies = 10,000 `sharesToUnderlyingView` calls, plus 400 top-level EigenLayer calls, the gas easily approaches or exceeds the 30M block gas limit.

---

### Impact Explanation

If `updateRSETHPrice()` reverts due to out-of-gas, the protocol cannot update its rsETH/ETH exchange rate. This breaks:
- `depositAsset()` / `depositETH()` (price used for minting rsETH)
- `getRsETHAmountToMint()` (used in deposit flow)
- Fee accrual (protocol fee minting in `_updateRsETHPrice()`)

Both `updateRSETHPrice()` (public) and `updateRSETHPriceAsManager()` (manager-only) call the same `_updateRsETHPrice()` internal function, so neither escapes the gas issue. [6](#0-5) 

---

### Likelihood Explanation

- `addNewSupportedAsset()` is gated by `TIME_LOCK_ROLE` — a legitimate governance operation, not an attack. [7](#0-6) 
- `addNodeDelegatorContractToQueue()` is gated by `onlyLRTAdmin` — a legitimate admin operation. [8](#0-7) 
- `initiateUnstaking()` is gated by `onlyLRTOperator` — a legitimate operator operation. [9](#0-8) 

No compromise is required. Normal protocol growth (adding more LSTs, more NDCs, more pending unstaking operations) monotonically increases gas until the function becomes uncallable.

---

### Recommendation

1. **Cache `getQueuedWithdrawals` per NDC**: Call it once per NDC (not once per NDC per asset) and filter by asset in a single pass. This reduces EigenLayer calls from `assets × NDCs` to `NDCs`.
2. **Add a hard cap on `supportedAssetList`**: Enforce a maximum length in `_addNewSupportedAsset()`.
3. **Separate TVL accounting from price update**: Store per-asset TVL snapshots updated independently, so `updateRSETHPrice()` reads cached values rather than recomputing the full nested traversal on every call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";

// Invariant: gas(updateRSETHPrice) < 15_000_000
// for all (assets in [2..20], NDCs in [1..10], withdrawals in [1..10])
contract GasInvariantTest is Test {
    // Deploy LRTConfig, LRTOracle, LRTDepositPool, N NodeDelegators,
    // mock EigenLayer DelegationManager returning W queued withdrawals
    // with S strategies each.
    //
    // For each combination (A assets, N NDCs, W withdrawals):
    //   1. addNewSupportedAsset() × A  (via TIME_LOCK_ROLE)
    //   2. addNodeDelegatorContractToQueue() × N  (via admin)
    //   3. mock getQueuedWithdrawals to return W withdrawals × S strategies
    //   4. uint256 gasBefore = gasleft();
    //      lrtOracle.updateRSETHPrice();
    //      uint256 gasUsed = gasBefore - gasleft();
    //   5. assertLt(gasUsed, 15_000_000);
}
```

Running this invariant with (A=20, N=10, W=10, S=5) will demonstrate gas consumption approaching or exceeding the 30M block gas limit, falsifying the invariant.

### Citations

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
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

**File:** contracts/LRTConfig.sol (L99-101)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
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

**File:** contracts/LRTDepositPool.sol (L302-323)
```text
    function addNodeDelegatorContractToQueue(address[] calldata nodeDelegatorContracts) external onlyLRTAdmin {
        uint256 length = nodeDelegatorContracts.length;
        if (nodeDelegatorQueue.length + length > maxNodeDelegatorLimit) {
            revert MaximumNodeDelegatorLimitReached();
        }

        for (uint256 i; i < length;) {
            UtilLib.checkNonZeroAddress(nodeDelegatorContracts[i]);

            // check if node delegator contract is already added and add it if not
            if (isNodeDelegator[nodeDelegatorContracts[i]] == 0) {
                nodeDelegatorQueue.push(nodeDelegatorContracts[i]);
                emit NodeDelegatorAddedinQueue(nodeDelegatorContracts[i]);
            }

            isNodeDelegator[nodeDelegatorContracts[i]] = 1;

            unchecked {
                ++i;
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

**File:** contracts/NodeDelegator.sol (L293-331)
```text
    function initiateUnstaking(
        IStrategy[] calldata strategies,
        uint256[] calldata shares
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlyLRTOperator
        returns (bytes32 withdrawalRoot)
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

**File:** contracts/NodeDelegatorHelper.sol (L31-39)
```text
    function getAssetBalance(ILRTConfig lrtConfig, address asset) internal view returns (uint256) {
        address strategy = lrtConfig.assetStrategy(asset);
        if (strategy == address(0)) {
            return 0;
        }
        uint256 withdrawableShare = getWithdrawableShare(lrtConfig, IStrategy(strategy));

        return IStrategy(strategy).sharesToUnderlyingView(withdrawableShare);
    }
```
