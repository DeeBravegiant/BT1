### Title
Unbounded `supportedAssetList` Iteration in `updateRSETHPrice()` Can Cause Permanent Gas Exhaustion - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that internally iterates over the entire `supportedAssetList` from `LRTConfig`. For each asset it calls `LRTDepositPool.getTotalAssetDeposits()`, which itself iterates over the entire `nodeDelegatorQueue`. There is no cap on `supportedAssetList` in `LRTConfig`. As the protocol adds more supported LSTs over time, this nested loop can exceed the block gas limit, permanently preventing rsETH price updates.

### Finding Description
`LRTOracle.updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction, making it callable by any external account. [1](#0-0) 

It delegates to `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`: [2](#0-1) 

`_getTotalEthInProtocol()` fetches the full `supportedAssetList` from `LRTConfig` and loops over every entry. For each asset it calls `ILRTDepositPool.getTotalAssetDeposits(asset)`, which calls `getAssetDistributionData(asset)`: [3](#0-2) 

`getAssetDistributionData` itself loops over the entire `nodeDelegatorQueue`, calling `getAssetBalance` and `getAssetUnstaking` on each NDC (which may make external calls into EigenLayer strategies). This creates a nested loop: **O(supportedAssets × nodeDelegators × EigenLayer calls)**.

`LRTConfig.supportedAssetList` has no maximum cap. Assets are added via `addNewSupportedAsset`, which only requires `TIME_LOCK_ROLE` and pushes unconditionally: [4](#0-3) 

There is no `MAX_SUPPORTED_ASSETS` constant or length check anywhere in the addition path.

### Impact Explanation
If `supportedAssetList` grows large enough (through legitimate governance adding new LSTs), `updateRSETHPrice()` will revert with out-of-gas on every call. Since `rsETHPrice` is a stored value updated only by this function, a permanent failure to call it means the rsETH/ETH exchange rate becomes permanently stale. Downstream, `getRsETHAmountToMint` reads this stale price, causing depositors to receive incorrect rsETH amounts. In the worst case, the price update path is permanently bricked, constituting a **Medium — Unbounded gas consumption** impact per the allowed scope.

### Likelihood Explanation
The protocol is actively expanding its supported LST set. Each new asset added by governance (via timelock) increases the per-call gas cost. No attacker action is required; ordinary protocol growth is sufficient. The risk compounds because `nodeDelegatorQueue` can also be expanded (up to `maxNodeDelegatorLimit`, which is admin-adjustable), multiplying the cost further. [5](#0-4) 

### Recommendation
Introduce a maximum cap on `supportedAssetList` in `LRTConfig`, analogous to `maxNodeDelegatorLimit` in `LRTDepositPool`:

```solidity
uint256 public constant MAX_SUPPORTED_ASSETS = 30;

function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
    if (supportedAssetList.length >= MAX_SUPPORTED_ASSETS) {
        revert MaxSupportedAssetsReached();
    }
    // ... existing logic
}
```

Additionally, consider caching `supportedAssetList` length and breaking the nested loop dependency by storing per-asset totals incrementally rather than recomputing them on every oracle update.

### Proof of Concept
1. Governance adds 50 supported LST assets to `LRTConfig` via `addNewSupportedAsset` (each call requires only `TIME_LOCK_ROLE`, no attacker involvement).
2. `maxNodeDelegatorLimit` is set to 10 (default) with 10 active NDCs.
3. Any external caller invokes `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` executes 50 × 10 = 500 iterations, each performing multiple external `STATICCALL`s into EigenLayer strategy contracts.
5. Total gas consumed exceeds the 30M block gas limit; the transaction reverts.
6. `rsETHPrice` is never updated; all subsequent deposits use a stale exchange rate. [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L290-296)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
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

**File:** contracts/LRTConfig.sol (L26-26)
```text
    address[] public supportedAssetList;
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
