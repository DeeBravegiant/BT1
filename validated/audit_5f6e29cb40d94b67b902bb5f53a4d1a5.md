### Title
Unbounded Nested Loop in `updateRSETHPrice()` Enables Block Stuffing — (`contracts/LRTOracle.sol`)

---

### Summary

`updateRSETHPrice()` is a permissionless `public` function. Its internal call chain contains a nested loop whose gas cost grows as **O(assets × NDCs)** with no on-chain cap. An unprivileged attacker can call it repeatedly within a single block to consume the block gas limit, constituting block stuffing.

---

### Finding Description

`updateRSETHPrice()` calls `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`. [1](#0-0) 

`_getTotalEthInProtocol()` iterates over every supported asset: [2](#0-1) 

For each asset it makes two external calls:
1. `getAssetPrice(asset)` — dispatches to an external price oracle (e.g., Chainlink `latestRoundData`)
2. `getTotalAssetDeposits(asset)` — calls `getAssetDistributionData`, which itself loops over every NDC: [3](#0-2) 

Each NDC iteration makes **3 external calls** (`balanceOf`, `getAssetBalance`, `getAssetUnstaking`). The total external call count is:

```
calls = N_assets × (1 oracle + 3 × N_NDCs)
```

With N=10 assets and M=10 NDCs → 310 external calls per invocation of `updateRSETHPrice()`.

There is no on-chain cap on `supportedAssetList` length: [4](#0-3) 

`maxNodeDelegatorLimit` defaults to 10 and is admin-adjustable upward: [5](#0-4) [6](#0-5) 

The attacker can call `updateRSETHPrice()` multiple times per block. Repeated calls succeed as long as the price does not increase above `pricePercentageLimit` (or if `pricePercentageLimit == 0`) and the daily fee mint limit is not exceeded. When TVL is flat between calls, `protocolFeeInETH == 0`, so `_checkAndUpdateDailyFeeMintLimit(0)` always passes: [7](#0-6) 

---

### Impact Explanation

**Low — Block stuffing.**

At realistic protocol scale (10 assets, 10 NDCs), a single `updateRSETHPrice()` call costs on the order of 1–3 M gas (310+ cold external calls at ≥2 100 gas each, plus EigenLayer strategy reads and Chainlink oracle reads). An attacker can pack ~10–30 such calls into a single 30 M gas block, crowding out all other transactions for that block. The attacker bears the gas cost themselves, but the capability exists without any privilege.

---

### Likelihood Explanation

**Low-to-Medium.** The precondition — many supported assets and NDCs — is a natural consequence of protocol growth, not an attack. `addNewSupportedAsset` requires `TIME_LOCK_ROLE` and `addNodeDelegatorContractToQueue` requires `onlyLRTAdmin`; both are expected to be exercised legitimately over time. No attacker-controlled state is required. The only cost to the attacker is gas.

---

### Recommendation

1. **Cache or snapshot** the total ETH value off-chain (keeper/bot pattern) and store it on-chain; `updateRSETHPrice()` reads the cached value instead of recomputing.
2. Alternatively, enforce a hard cap on `supportedAssetList.length` and `maxNodeDelegatorLimit` such that the worst-case gas of `updateRSETHPrice()` is provably below a safe fraction of the block gas limit.
3. Add a `cooldown` (e.g., minimum 1 block between calls) to prevent repeated invocations within the same block.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

// Foundry fork test (local fork, no mainnet)
import "forge-std/Test.sol";

contract BlockStuffingPoC is Test {
    ILRTOracle oracle = ILRTOracle(<deployed_oracle>);

    function testGasGrowth() public {
        // Precondition: 10 assets, 10 NDCs registered by admin (local setup)
        uint256 gasBefore = gasleft();
        oracle.updateRSETHPrice();
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("gas per call (10 assets x 10 NDCs)", gasUsed);

        // Attacker fills block: call repeatedly until block gas limit approached
        uint256 blockGasLimit = 30_000_000;
        uint256 callCount = blockGasLimit / gasUsed;
        emit log_named_uint("calls to fill block", callCount);
        // assert callCount is small (e.g., < 30), confirming block stuffing feasibility
        assertLt(callCount, 30);
    }
}
```

The fuzz invariant `assert gas(updateRSETHPrice) grows super-linearly` can be confirmed by parameterizing `assetCount` in [1..20] and `ndcCount` in [1..10] in a local Foundry test, measuring `gasleft()` before and after each call.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L309-311)
```text
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
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

**File:** contracts/LRTDepositPool.sol (L49-49)
```text
        maxNodeDelegatorLimit = 10;
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
