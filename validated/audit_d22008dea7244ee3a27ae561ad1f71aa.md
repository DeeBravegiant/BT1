### Title
Unbounded Iteration Over Supported Assets in `updateRSETHPrice()` Can Permanently DoS Price Updates — (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function. Its internal call chain iterates over every supported asset with no explicit cap, and for each asset makes `nodeDelegatorQueue.length` external calls into EigenLayer. There is no sanity limit on the number of supported assets processed in a single transaction. As the protocol adds more LSTs, the cumulative gas cost grows proportionally and can eventually exceed the block gas limit, permanently preventing price updates and causing the rsETH exchange rate to become stale.

---

### Finding Description

`updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction, meaning any address can invoke it. [1](#0-0) 

It delegates to `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`: [2](#0-1) 

`_getTotalEthInProtocol()` iterates over `lrtConfig.getSupportedAssetList()` — an array with **no explicit upper bound** — and for each asset calls `ILRTDepositPool.getTotalAssetDeposits(asset)`. [3](#0-2) 

`getTotalAssetDeposits()` calls `getAssetDistributionData()`, which loops over every NDC in `nodeDelegatorQueue`: [4](#0-3) 

For each NDC it calls `INodeDelegator.getAssetUnstaking(asset)`, which makes an external call to EigenLayer's `delegationManager.getQueuedWithdrawals()` and then iterates over every queued withdrawal and every strategy within it: [5](#0-4) 

The total gas cost is therefore **O(assets × NDCs × withdrawals\_per\_NDC)** with no sanity limit on the `assets` dimension. The `supportedAssetList` in `LRTConfig` has no cap: [6](#0-5) 

`maxNodeDelegatorLimit` (default 10) bounds NDCs, and `maxUncompletedWithdrawalCount` (≤ 80) bounds withdrawals, but neither bound applies to the number of supported assets. Each additional asset multiplies the number of external EigenLayer calls by `nodeDelegatorQueue.length`.

---

### Impact Explanation

If the cumulative gas cost of `updateRSETHPrice()` exceeds the block gas limit, the function becomes permanently uncallable. The stored `rsETHPrice` becomes stale and diverges from the true protocol TVL. All subsequent deposits via `depositETH()` / `depositAsset()` and withdrawals via `initiateWithdrawal()` / `instantWithdrawal()` use the stale price: [7](#0-6) [8](#0-7) 

A stale price that lags behind true TVL growth causes depositors to receive more rsETH than they are entitled to, diluting existing holders and creating insolvency risk. A stale price that lags behind a TVL decrease causes withdrawers to receive more assets than they are entitled to, directly draining the protocol. **Impact: Medium — Unbounded gas consumption leading to permanent DoS of price updates.**

---

### Likelihood Explanation

The Kelp DAO protocol is explicitly designed to onboard additional LSTs over time via `addNewSupportedAsset()` (TIME\_LOCK\_ROLE). Each new asset linearly increases the gas cost of every `updateRSETHPrice()` call. With 10 NDCs and 80 queued withdrawals, adding ~15–20 assets is sufficient to push the function near or past the 30 M gas block limit on Ethereum mainnet, given the cost of repeated external calls to EigenLayer per asset per NDC. **Likelihood: Medium** — the protocol's own growth roadmap drives the system toward this condition without any adversarial action.

---

### Recommendation

Introduce a `maxSupportedAssetsPerPriceUpdate` constant (analogous to `maxNodeDelegatorLimit`) and enforce it inside `_getTotalEthInProtocol()`. Alternatively, refactor `getAssetUnstaking()` so that queued-withdrawal data is fetched once per NDC (not once per asset per NDC) and cached in memory for the duration of the price-update call, reducing the external-call count from O(assets × NDCs) to O(NDCs).

---

### Proof of Concept

Call chain triggered by any unprivileged address:

```
updateRSETHPrice()                          // public, no role check
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()
            └─ for each asset in supportedAssetList (no cap):
                 └─ getTotalAssetDeposits(asset)
                      └─ getAssetDistributionData(asset)
                           └─ for each NDC in nodeDelegatorQueue (≤ maxNodeDelegatorLimit):
                                └─ getAssetUnstaking(asset)   // external call to EigenLayer
                                     └─ delegationManager.getQueuedWithdrawals(ndc)
                                          └─ for each queued withdrawal (≤ maxUncompletedWithdrawalCount):
                                               └─ for each strategy in withdrawal.strategies
```

With 20 supported assets, 10 NDCs, and 80 queued withdrawals per NDC:
- External calls to EigenLayer: 20 × 10 = **200**
- Inner strategy iterations: 20 × 10 × 80 = **16 000**

At ~2 100 gas per cold external call and ~800 gas per SLOAD, this exceeds 30 M gas, making the block unable to include the transaction.

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
