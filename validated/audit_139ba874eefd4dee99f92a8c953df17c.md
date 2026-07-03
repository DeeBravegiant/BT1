### Title
O(A×N) Unbounded External Calls in `updateRSETHPrice()` Can Permanently Freeze rsETH Price — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function. Its internal call chain makes **A × N** external calls to EigenLayer's `DelegationManager.getWithdrawableShares()`, where A = number of supported assets and N = number of NodeDelegators. Neither dimension has a hard protocol cap. As the protocol grows, the cumulative gas cost can exceed the block gas limit, permanently preventing any price update.

---

### Finding Description

The full call chain is:

```
updateRSETHPrice()                          [public, no role check]
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()          [loops over A assets]
            └─ getTotalAssetDeposits(asset)
                 └─ getAssetDistributionData(asset)
                      └─ [loops over N NDCs]
                           ├─ getAssetBalance(asset)           [per NDC]
                           │    └─ NodeDelegatorHelper.getAssetBalance()
                           │         └─ getWithdrawableShare()
                           │              └─ DelegationManager.getWithdrawableShares()  ← external call
                           └─ getAssetUnstaking(asset)         [per NDC, additional external call]
```

**Step 1 — `_getTotalEthInProtocol` loops over all supported assets:** [1](#0-0) 

**Step 2 — `getAssetDistributionData` loops over all NDCs per asset, making two external calls per NDC:** [2](#0-1) 

**Step 3 — Each `getAssetBalance` call reaches `DelegationManager.getWithdrawableShares` via `NodeDelegatorHelper`:** [3](#0-2) 

**No hard cap on either dimension:**

- `maxNodeDelegatorLimit` initializes to 10 but `updateMaxNodeDelegatorLimit` accepts any value ≥ current queue length — no upper bound: [4](#0-3) 

- `addNewSupportedAsset` pushes to `supportedAssetList` with no length cap: [5](#0-4) 

---

### Impact Explanation

If the cumulative gas of A × N `getWithdrawableShares` calls (plus surrounding overhead) exceeds the block gas limit (~30M gas on Ethereum mainnet), every call to `updateRSETHPrice()` reverts with out-of-gas. Since `rsETHPrice` is only updated through this function, the stored price becomes permanently stale. Downstream flows that depend on a fresh price (e.g., `getRsETHAmountToMint` using the stored `rsETHPrice`) will silently use an outdated rate, and the protocol loses its ability to ever correct the price without a contract upgrade.

**Impact: Medium — Unbounded gas consumption / permanent freezing of price update.**

---

### Likelihood Explanation

- `updateRSETHPrice()` is public with no access control — any EOA can call it. [6](#0-5) 
- The protocol already supports multiple LST assets (stETH, ETHx, etc.) and is designed to add more via governance.
- `maxNodeDelegatorLimit` has no on-chain ceiling; operators routinely increase it as the protocol scales.
- No gas-limit guard or batching exists anywhere in the price-update path.

At A=10 assets × N=10 NDCs = 100 cold external calls to EigenLayer, each costing ~2,100 gas (CALL opcode) plus EigenLayer's own storage reads (~5,000–20,000 gas per call), the total easily reaches 1–2M gas. At A=20 × N=20 = 400 calls the estimate reaches 8–16M gas. At A=30 × N=30 = 900 calls it exceeds 30M. These are reachable configurations given the absence of any hard cap.

---

### Recommendation

1. **Batch EigenLayer queries per NDC**: Instead of calling `getWithdrawableShares` once per (NDC, asset) pair, call it once per NDC with all strategies in a single array. `NodeDelegatorHelper.getWithdrawableShares` already accepts `IStrategy[] memory strategies` — use it. [7](#0-6) 

2. **Enforce a hard cap** on both `maxNodeDelegatorLimit` and `supportedAssetList.length` at values that keep worst-case gas well below the block limit.

3. **Add a gas guard** in `updateRSETHPrice()` (e.g., `require(gasleft() > MIN_GAS_REQUIRED)`) to fail fast with a meaningful error rather than an opaque out-of-gas revert.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

// Invariant: for all (A, N) in [1..30] x [1..30],
// gas consumed by updateRSETHPrice() < 15_000_000

// Fork test (local hardhat/foundry fork):
// 1. Deploy LRTConfig with A supported assets (each with a mock strategy).
// 2. Deploy LRTDepositPool and add N NodeDelegator contracts.
// 3. Call updateRSETHPrice() and measure gasleft() delta.
// 4. Assert gas < 15_000_000.
//
// Expected result at A=20, N=20:
//   - 400 calls to DelegationManager.getWithdrawableShares
//   - ~400 * 15_000 gas = 6_000_000 gas for EigenLayer calls alone
//   - Plus loop overhead, SLOAD, CALL setup ≈ 10_000_000+ total
//   - Approaches or exceeds 15_000_000 well before A=N=30
```

The concrete path through unmodified production code is:

`updateRSETHPrice` → `_getTotalEthInProtocol` (A iterations) → `getAssetDistributionData` (N iterations each) → `NodeDelegatorHelper.getAssetBalance` → `getWithdrawableShare` → `DelegationManager.getWithdrawableShares`. [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
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

**File:** contracts/NodeDelegatorHelper.sol (L31-50)
```text
    function getAssetBalance(ILRTConfig lrtConfig, address asset) internal view returns (uint256) {
        address strategy = lrtConfig.assetStrategy(asset);
        if (strategy == address(0)) {
            return 0;
        }
        uint256 withdrawableShare = getWithdrawableShare(lrtConfig, IStrategy(strategy));

        return IStrategy(strategy).sharesToUnderlyingView(withdrawableShare);
    }

    function getWithdrawableShares(
        ILRTConfig lrtConfig,
        IStrategy[] memory strategies
    )
        internal
        view
        returns (uint256[] memory withdrawableShares)
    {
        (withdrawableShares,) = getDelegationManager(lrtConfig).getWithdrawableShares(address(this), strategies);
    }
```

**File:** contracts/NodeDelegatorHelper.sol (L52-65)
```text
    function getWithdrawableShare(
        ILRTConfig lrtConfig,
        IStrategy strategy
    )
        internal
        view
        returns (uint256 withdrawableShare)
    {
        IStrategy[] memory strategies = new IStrategy[](1);
        strategies[0] = strategy;

        uint256[] memory withdrawableShares = getWithdrawableShares(lrtConfig, strategies);
        return withdrawableShares[0];
    }
```

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
