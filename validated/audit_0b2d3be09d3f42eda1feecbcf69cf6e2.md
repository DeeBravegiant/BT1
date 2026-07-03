Audit Report

## Title
Beacon-chain slashing deficit silently dropped in `getEffectivePodShares`, inflating rsETHPrice above true backing — (`contracts/NodeDelegator.sol`)

## Summary
When a NodeDelegator's `podOwnerDepositShares` goes negative due to severe beacon-chain slashing, `DelegationManager.getWithdrawableShares` returns 0 (clamped). `NodeDelegator.getEffectivePodShares` then returns `stakedButUnverifiedNativeETH + 0`, silently discarding the negative-share deficit. This causes `LRTOracle` to overcount ETH in the protocol and store an `rsETHPrice` above the true backing ratio, distorting withdrawal payouts among rsETH holders.

## Finding Description

`NodeDelegator.getEffectivePodShares` at lines 556–562 reads:

```solidity
function getEffectivePodShares() external view override returns (uint256 ethStaked) {
    uint256 withdrawableShare =
        NodeDelegatorHelper.getWithdrawableShare(lrtConfig, IStrategy(lrtConfig.beaconChainETHStrategy()));
    // staker balances can no longer be negative
    return stakedButUnverifiedNativeETH + withdrawableShare;
}
``` [1](#0-0) 

`NodeDelegatorHelper.getWithdrawableShare` delegates to `DelegationManager.getWithdrawableShares`: [2](#0-1) 

Both `IShareManager` and `IEigenPodManager` document that `stakerDepositShares` returns 0 when `podOwnerDepositShares` is negative: [3](#0-2) [4](#0-3) 

The `IEigenPodManagerErrors` interface confirms negative shares are a reachable state (`SharesNegative`, `LegacyWithdrawalsNotCompleted`). [5](#0-4) 

When `podOwnerDepositShares = -D` (deficit D), `withdrawableShare` is clamped to 0, and `getEffectivePodShares` returns `stakedButUnverifiedNativeETH` as if the deficit does not exist. The full call chain propagating this inflated value:

1. `LRTDepositPool.getETHDistributionData` sums `getEffectivePodShares()` across all NDCs into `ethStakedInEigenLayer`: [6](#0-5) 

2. `getTotalAssetDeposits(ETH)` calls `getAssetDistributionData` → `getETHDistributionData` and sums all components: [7](#0-6) 

3. `LRTOracle._getTotalEthInProtocol` calls `getTotalAssetDeposits` for each supported asset: [8](#0-7) 

4. `_updateRsETHPrice` computes `newRsETHPrice = (totalETHInProtocol - fee) / rsethSupply` and stores it: [9](#0-8) 

5. `LRTWithdrawalManager.getExpectedAssetAmount` uses the stored `rsETHPrice` to compute ETH per rsETH burned: [10](#0-9) 

The downside-protection check compares `newRsETHPrice` against `highestRsethPrice`. Because the overcounting inflates `newRsETHPrice`, the apparent price drop is smaller than the true drop, potentially suppressing the automatic pause: [11](#0-10) 

## Impact Explanation

The overcounting equals `|podOwnerDepositShares|` (the deficit D). `rsETHPrice` is inflated by `D / rsETH_supply`. Users who initiate withdrawals while the inflated price is stored receive more ETH per rsETH than the protocol can actually back. Later withdrawers receive proportionally less, violating the invariant that all rsETH holders are backed equally. No funds leave the system externally, but the distribution among holders is distorted. A secondary effect is that the automatic pause protection in `_updateRsETHPrice` may fail to trigger because the computed price drop is smaller than the true drop.

This matches **Low: Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation

Beacon-chain slashing is a known production risk. `podOwnerDepositShares` goes negative when a withdrawal was queued before a checkpoint that records a large slashing loss — a realistic sequence during a correlated slashing event. Having `stakedButUnverifiedNativeETH > 0` simultaneously (validators staked but not yet credential-verified) is normal operational state. The combination is uncommon but entirely plausible in production. No privileged action or attacker is required; the condition arises from beacon-chain events outside the protocol's control.

## Recommendation

In `getEffectivePodShares`, read `podOwnerDepositShares` directly from `IEigenPodManager` and subtract any negative deficit from `stakedButUnverifiedNativeETH`:

```solidity
function getEffectivePodShares() external view override returns (uint256 ethStaked) {
    uint256 withdrawableShare =
        NodeDelegatorHelper.getWithdrawableShare(lrtConfig, IStrategy(lrtConfig.beaconChainETHStrategy()));

    int256 depositShares = _getEigenPodManager().podOwnerDepositShares(address(this));
    if (depositShares < 0) {
        uint256 deficit = uint256(-depositShares);
        return stakedButUnverifiedNativeETH > deficit
            ? stakedButUnverifiedNativeETH - deficit + withdrawableShare
            : 0;
    }
    return stakedButUnverifiedNativeETH + withdrawableShare;
}
```

## Proof of Concept

```solidity
// Foundry unit test (mock-based)
// 1. Deploy NDC with mock EigenPodManager and mock DelegationManager
// 2. Set stakedButUnverifiedNativeETH = 32 ether
// 3. Mock DelegationManager.getWithdrawableShares → returns [0]
//    (simulating podOwnerDepositShares = -10 ether after slashing)
// 4. Call getEffectivePodShares() → returns 32 ether
// 5. True backing = 32 - 10 = 22 ether
// 6. Compute rsETHPrice from 32 ether vs 22 ether
//    → confirms ~45% price inflation above true backing
// 7. Assert that _updateRsETHPrice stores the inflated price
// 8. Assert that getExpectedAssetAmount returns more ETH than available
//    for an early withdrawer, leaving a shortfall for later withdrawers
```

### Citations

**File:** contracts/NodeDelegator.sol (L556-562)
```text
    function getEffectivePodShares() external view override returns (uint256 ethStaked) {
        uint256 withdrawableShare =
            NodeDelegatorHelper.getWithdrawableShare(lrtConfig, IStrategy(lrtConfig.beaconChainETHStrategy()));

        // staker balances can no longer be negative
        return stakedButUnverifiedNativeETH + withdrawableShare;
    }
```

**File:** contracts/NodeDelegatorHelper.sol (L41-65)
```text
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

**File:** contracts/external/eigenlayer/interfaces/IShareManager.sol (L41-44)
```text
    /// @notice Returns the current shares of `user` in `strategy`
    /// @dev strategy must be beaconChainETH when talking to the EigenPodManager
    /// @dev returns 0 if the user has negative shares
    function stakerDepositShares(address user, IStrategy strategy) external view returns (uint256 depositShares);
```

**File:** contracts/external/eigenlayer/interfaces/IEigenPodManager.sol (L19-27)
```text
    /// @dev Thrown when shares is not a multiple of gwei.
    error SharesNotMultipleOfGwei();
    /// @dev Thrown when shares would result in a negative integer.
    error SharesNegative();
    /// @dev Thrown when the strategy is not the beaconChainETH strategy.
    error InvalidStrategy();
    /// @dev Thrown when the pods shares are negative and a beacon chain balance update is attempted.
    /// The podOwner should complete legacy withdrawal first.
    error LegacyWithdrawalsNotCompleted();
```

**File:** contracts/external/eigenlayer/interfaces/IEigenPodManager.sol (L157-160)
```text
    /// @notice Returns the current shares of `user` in `strategy`
    /// @dev strategy must be beaconChainETH when talking to the EigenPodManager
    /// @dev returns 0 if the user has negative shares.
    function stakerDepositShares(address user, IStrategy strategy) external view returns (uint256 depositShares);
```

**File:** contracts/LRTDepositPool.sol (L385-396)
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
```

**File:** contracts/LRTDepositPool.sol (L484-493)
```text
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

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
