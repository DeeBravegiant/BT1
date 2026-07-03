### Title
EigenLayer Slashing Losses on Queued Withdrawals Not Reflected in Protocol TVL, Enabling Protocol Insolvency - (File: contracts/NodeDelegator.sol)

### Summary

`NodeDelegator.getAssetUnstaking` reports the pre-slashing `scaledShares` amount for queued EigenLayer withdrawals without applying the current slashing factor. This inflates `getTotalAssetDeposits`, which in turn inflates the rsETH price reported by `LRTOracle` and the available withdrawal capacity tracked by `LRTWithdrawalManager`. When slashing occurs after a withdrawal is queued but before it completes, the vault receives fewer assets than the protocol accounted for, leaving committed withdrawals underfunded — a direct analog to unaccounted bad debt.

### Finding Description

`NodeDelegator.getAssetUnstaking` queries EigenLayer's `DelegationManager.getQueuedWithdrawals` and converts the returned `scaledShares` to an asset amount:

```solidity
uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
amount += strategyAsset == LRTConstants.ETH_TOKEN
    ? sharesToUnstake
    : strategy.sharesToUnderlyingView(sharesToUnstake);
``` [1](#0-0) 

In EigenLayer's slashing model (as encoded in `SlashingLib`), the actual tokens received when completing a withdrawal are `scaledShares * slashingFactor`, where `slashingFactor` can decrease after the withdrawal is queued if the delegated operator is slashed:

```solidity
function scaleForCompleteWithdrawal(uint256 scaledShares, uint256 slashingFactor) internal pure returns (uint256) {
    return scaledShares.mulWad(slashingFactor);
}
``` [2](#0-1) 

`getAssetUnstaking` does not apply the slashing factor, so it overestimates the assets in transit. This overestimate propagates through the entire accounting stack:

**Step 1 — `getTotalAssetDeposits` is inflated:**

```solidity
assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
...
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + ...);
``` [3](#0-2) 

**Step 2 — `LRTOracle` reports an inflated rsETH price:**

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [4](#0-3) 

The inflated `totalETHInProtocol` causes `rsETHPrice` to be set higher than the true backing, so users who withdraw at this price receive more underlying assets than they are entitled to.

**Step 3 — `getAvailableAssetAmount` is inflated, allowing over-commitment:**

```solidity
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
``` [5](#0-4) 

`initiateWithdrawal` uses this to gate new withdrawal commitments:

```solidity
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
``` [6](#0-5) 

Because `getAvailableAssetAmount` is inflated, the protocol accepts more withdrawal commitments than it can actually fulfill. When the slashed EigenLayer withdrawal completes and delivers fewer tokens to `LRTUnstakingVault`, there are insufficient assets to honor all committed withdrawals.

### Impact Explanation

**Critical — Protocol Insolvency.**

When EigenLayer slashing reduces the actual tokens received from a queued withdrawal:
1. The rsETH price remains artificially high, so early withdrawers extract more underlying assets than their rsETH is truly worth.
2. The `assetsCommitted` accounting allows more withdrawal requests than the vault can fulfill.
3. Later withdrawers find the vault insolvent — their committed withdrawals cannot be completed.

The loss is borne by remaining rsETH holders and late withdrawers, exactly mirroring the bad-debt dynamic in the reference report.

### Likelihood Explanation

EigenLayer slashing is an explicitly modeled risk in this codebase — the protocol imports and uses `SlashingLib`, and `NodeDelegator` handles `burnOperatorShares` events. The protocol delegates to external EigenLayer operators, any of whom can be slashed by an AVS. No privileged access or attacker action is required to trigger the slashing; it is a normal protocol event. The window between `initiateUnstaking` and `completeUnstaking` (EigenLayer's withdrawal delay, typically days) is the exposure period.

### Recommendation

Apply the current slashing factor when computing the expected asset amount from queued withdrawals in `getAssetUnstaking`. Query the operator's current `maxMagnitude` (or the staker's `beaconChainSlashingFactor` for native ETH) from EigenLayer's `AllocationManager`/`EigenPodManager` and scale `scaledShares` accordingly before summing into the protocol's TVL. Alternatively, use a conservative lower-bound estimate (e.g., track the minimum slashing factor seen since queuing) to ensure the protocol never over-commits withdrawal capacity.

### Proof of Concept

1. Protocol has 1000 stETH staked in EigenLayer via a `NodeDelegator`, delegated to operator O.
2. Operator calls `initiateUnstaking` for 1000 stETH shares → `scaledShares = 1000e18` stored in withdrawal struct.
3. AVS slashes operator O by 20% → `slashingFactor` drops to `0.8e18`.
4. `getAssetUnstaking` still returns `sharesToUnderlyingView(1000e18) = 1000 stETH` (no slashing factor applied).
5. `getTotalAssetDeposits(stETH)` reports 1000 stETH; true recoverable amount is 800 stETH.
6. `LRTOracle.rsETHPrice` is inflated by ~20%.
7. User A calls `initiateWithdrawal` for rsETH worth 800 stETH at the inflated price → committed.
8. User B calls `initiateWithdrawal` for rsETH worth 200 stETH at the inflated price → committed (total committed = 1000 stETH, matching the inflated `getAvailableAssetAmount`).
9. `completeUnstaking` delivers only 800 stETH to `LRTUnstakingVault`.
10. `unlockQueue` can only fulfill 800 stETH of the 1000 stETH committed → User B's withdrawal cannot be processed → protocol insolvency. [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** contracts/external/eigenlayer/libraries/SlashingLib.sol (L82-84)
```text
    function scaleForCompleteWithdrawal(uint256 scaledShares, uint256 slashingFactor) internal pure returns (uint256) {
        return scaledShares.mulWad(slashingFactor);
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

**File:** contracts/LRTDepositPool.sol (L451-396)
```text

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

**File:** contracts/LRTWithdrawalManager.sol (L168-178)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```
