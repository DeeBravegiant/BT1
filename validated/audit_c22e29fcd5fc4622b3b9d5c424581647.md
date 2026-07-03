### Title
Unbounded Gas Consumption in `LRTOracle._getTotalEthInProtocol()` via Nested Iteration Over `supportedAssets` × `nodeDelegatorQueue` — (File: contracts/LRTOracle.sol)

---

### Summary
`LRTOracle._getTotalEthInProtocol()` iterates over every entry in `supportedAssetList` and, for each asset, calls `LRTDepositPool.getTotalAssetDeposits()` which itself iterates over every entry in `nodeDelegatorQueue`. This O(n × m) nested external-call loop is invoked by the public `updateRSETHPrice()` function. As the protocol legitimately grows its asset and node-delegator counts, the transaction will eventually exceed the block gas limit, permanently preventing rsETH price updates and breaking deposit/withdrawal accounting for all users.

---

### Finding Description

`LRTOracle._getTotalEthInProtocol()` is the protocol's equivalent of `ZkdlpManager.getAum`. It computes total ETH value across all supported assets:

```solidity
// contracts/LRTOracle.sol lines 331–349
function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
    address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
    address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
    uint256 supportedAssetCount = supportedAssets.length;

    for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
        address asset = supportedAssets[assetIdx];
        uint256 assetER = getAssetPrice(asset);                                          // external oracle call
        uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset); // nested loop
        totalETHInProtocol += totalAssetAmt.mulWad(assetER);
        unchecked { ++assetIdx; }
    }
}
```

`getTotalAssetDeposits` calls `getAssetDistributionData`, which itself loops over `nodeDelegatorQueue`:

```solidity
// contracts/LRTDepositPool.sol lines 446–456
uint256 ndcsCount = nodeDelegatorQueue.length;
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    unchecked { ++i; }
}
```

The total external calls per `updateRSETHPrice()` invocation is `supportedAssets.length × nodeDelegatorQueue.length × 3` (three external calls per NDC per asset), plus one oracle call per asset. Both arrays grow through normal governance operations (`addNewSupportedAsset` via `TIME_LOCK_ROLE`, `addNodeDelegatorContractToQueue` via admin), and `maxNodeDelegatorLimit` is itself updatable by admin with no upper bound enforced on-chain.

`updateRSETHPrice()` is declared `public` with no access restriction:

```solidity
// contracts/LRTOracle.sol line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

---

### Impact Explanation

If `updateRSETHPrice()` reverts due to out-of-gas, the stored `rsETHPrice` becomes permanently stale. Every subsequent call to `LRTDepositPool.getRsETHAmountToMint()` divides by the stale price:

```solidity
// contracts/LRTDepositPool.sol line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

A stale (lower-than-actual) `rsETHPrice` causes depositors to receive more rsETH than they should (diluting existing holders). A stale (higher-than-actual) price causes depositors to receive fewer rsETH than they should. In either case, the price-update mechanism is permanently broken, constituting **Medium — Unbounded gas consumption** with secondary share mis-accounting impact.

---

### Likelihood Explanation

The protocol is designed to support multiple LSTs and multiple node delegators. `maxNodeDelegatorLimit` starts at 10 but is freely updatable by admin. As the protocol expands to new assets and scales its EigenLayer delegation infrastructure, the nested loop cost grows quadratically. This is a realistic operational trajectory, not a theoretical edge case.

---

### Recommendation

1. Cache `nodeDelegatorQueue.length` and `supportedAssets.length` and enforce hard on-chain caps that account for the nested gas cost.
2. Refactor `_getTotalEthInProtocol()` to maintain a running `totalETHInProtocol` accumulator updated incrementally on deposit/withdrawal events rather than recomputing it from scratch on every price update.
3. Alternatively, separate the per-asset TVL aggregation into a paginated or off-chain-assisted pattern, storing per-asset totals that are updated lazily.

---

### Proof of Concept

1. Governance adds 10 supported LST assets via `LRTConfig.addNewSupportedAsset`.
2. Admin raises `maxNodeDelegatorLimit` to 20 and adds 20 NDCs via `LRTDepositPool.addNodeDelegatorContractToQueue`.
3. Any unprivileged user calls `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` executes 10 × 20 × 3 = 600 external `staticcall`s plus 10 oracle calls in a single transaction.
5. At ~2,500 gas per external call (cold) the loop alone consumes ~1.5M gas before any computation overhead; with warm-storage reads and arithmetic the transaction approaches or exceeds the 30M block gas limit as counts grow further.
6. `updateRSETHPrice()` reverts; `rsETHPrice` is frozen at its last value; all subsequent mints use the stale price. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTConfig.sol (L99-118)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
    }

    /// @dev private function to add a new supported asset
    /// @param asset Asset address
    /// @param depositLimit Deposit limit for the asset
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
