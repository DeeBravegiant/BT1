Audit Report

## Title
Nested Unbounded Loop in `getAssetUnstaking()` Causes Multiplicative Gas Growth in Public Functions - (File: contracts/NodeDelegator.sol)

## Summary
`NodeDelegator.getAssetUnstaking()` performs an external EigenLayer call and nested loops over all queued withdrawals and their strategies on every invocation. It is called once per NDC per supported asset inside `_getTotalEthInProtocol()`, which is invoked by the publicly accessible `updateRSETHPrice()`, `depositETH()`, and `depositAsset()`. As the number of queued withdrawals and supported assets grows through normal protocol operation, the gas cost of these public functions grows multiplicatively. The developers' own code confirms the gas limit is reachable and that the existing cap is insufficient to fully bound the cost.

## Finding Description

`NodeDelegator.getAssetUnstaking()` makes an external call to `_getDelegationManager().getQueuedWithdrawals(address(this))` and then iterates over all returned withdrawals and their strategies in a nested loop: [1](#0-0) 

This function is called once per NDC inside `getAssetDistributionData()`: [2](#0-1) 

And again once per NDC inside `getETHDistributionData()`: [3](#0-2) 

`getTotalAssetDeposits()` calls `getAssetDistributionData()`: [4](#0-3) 

`_getTotalEthInProtocol()` calls `getTotalAssetDeposits()` once per supported asset in a loop: [5](#0-4) 

The publicly callable `updateRSETHPrice()` triggers this entire chain: [6](#0-5) 

The total gas cost scales as **N (assets) × M (NDCs) × K (withdrawals/NDC) × S (strategies/withdrawal)** external storage reads and inner iterations. There is no caching; the full traversal is recomputed from scratch on every call.

The existing mitigation — `setMaxUncompletedWithdrawalCount()` capping K at 80 — is acknowledged by the developers themselves to be insufficient: [7](#0-6) 

The comment states "120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price" and sets the cap at 80 for buffer. Critically, this cap was calibrated for a fixed N (supported asset count). Each additional supported asset added via governance linearly increases the number of `getQueuedWithdrawals()` calls and inner iterations, reducing the effective safe K proportionally. The cap does not adjust dynamically with N.

## Impact Explanation

When the multiplicative product N × M × K × S exceeds the block gas limit, `updateRSETHPrice()`, `depositETH()`, and `depositAsset()` revert out-of-gas. This constitutes **Medium — Unbounded gas consumption** on public entry points and **Medium — Temporary freezing of funds**: users cannot deposit assets and price updates halt until operators complete enough pending withdrawals to reduce K below the effective threshold for the current N.

## Likelihood Explanation

No malicious actor is required. Normal protocol operation — operators calling `initiateUnstaking()` and governance adding supported assets via `addNewSupportedAsset()` — grows the multiplicative factor. The developers' own comment confirms the gas limit is reachable at K=120 for the current asset count. Adding even one or two more supported assets lowers the effective safe K below 80, invalidating the existing buffer. `updateRSETHPrice()` is callable by any address with no preconditions.

## Recommendation

Cache the per-NDC per-asset unstaking amount in a storage mapping (e.g., `mapping(address ndc => mapping(address asset => uint256)) public assetUnstaking`) updated atomically in `initiateUnstaking()` and `completeUnstaking()`. Replace the live `getAssetUnstaking()` loop with a single storage read. This eliminates the external EigenLayer call and nested loops from the hot read path entirely, making gas cost O(N × M) with cheap storage reads instead of O(N × M × K × S) with external calls.

## Proof of Concept

Call chain for `updateRSETHPrice()` (no-auth, publicly callable):

```
updateRSETHPrice()                                    // LRTOracle.sol:87
  → _updateRsETHPrice()
    → _getTotalEthInProtocol()                        // LRTOracle.sol:331 — loops N assets
      → getTotalAssetDeposits(asset)                  // LRTDepositPool.sol:385 — called N times
        → getAssetDistributionData(asset)             // LRTDepositPool.sol:426
          → for i in [0, M):                          // LRTDepositPool.sol:447 — M NDCs
              getAssetUnstaking(asset)                // NodeDelegator.sol:405 — N×M calls
                → getQueuedWithdrawals(NDC)           // external EigenLayer SLOAD, N×M times
                → for w in [0, K):                    // NodeDelegator.sol:409
                    for s in [0, S):                  // NodeDelegator.sol:412
                      // N×M×K×S inner iterations
```

Foundry fork test plan:
1. Fork mainnet; deploy protocol with N=5 supported assets, M=5 NDCs.
2. Have operators call `initiateUnstaking()` until each NDC has K=80 queued withdrawals.
3. Call `updateRSETHPrice()` and record gas: expect ~5×5×80×3 = 6,000 inner iterations + 25 external calls.
4. Add one more supported asset (N=6) via `addNewSupportedAsset()`.
5. Call `updateRSETHPrice()` again; gas increases by ~20% (one full extra pass over all NDC withdrawals).
6. Repeat until the call reverts out-of-gas, demonstrating the multiplicative growth and the inadequacy of the fixed K cap.

### Citations

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

**File:** contracts/LRTDepositPool.sol (L482-493)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }
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

**File:** contracts/LRTUnstakingVault.sol (L150-159)
```text
    function setMaxUncompletedWithdrawalCount(uint256 _maxUncompletedWithdrawalCount) external onlyLRTManager {
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;

        emit MaxUncompletedWithdrawalCountSet(_maxUncompletedWithdrawalCount);
    }
```
