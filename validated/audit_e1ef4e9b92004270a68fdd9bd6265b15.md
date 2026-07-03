Audit Report

## Title
`stakedButUnverifiedNativeETH` Not Adjusted for Beacon-Chain Slashing Causes rsETH Price Inflation and Protocol Pause - (File: contracts/NodeDelegator.sol)

## Summary

`NodeDelegator` tracks ETH staked to beacon-chain validators in `stakedButUnverifiedNativeETH`, always incrementing by exactly 32 ETH on `stake32Eth` and decrementing by exactly 32 ETH on `verifyWithdrawalCredentials`, with no mechanism to reflect beacon-chain slashing. If a validator is slashed before credential verification, the protocol's TVL accounting overstates real backing, inflating rsETH price during the unverified window. When credentials are verified, the counter drops by 32 ETH while EigenLayer only credits the actual (post-slash) balance, producing a net TVL decrease that can trigger the oracle's auto-pause, temporarily freezing all deposits and withdrawals.

## Finding Description

**Step 1 – `stake32Eth` unconditionally increments the counter by 32 ETH.** [1](#0-0) 

The counter has no on-chain link to the beacon chain and cannot self-correct when a validator is slashed.

**Step 2 – `getEffectivePodShares` sums the stale counter with EigenLayer's live withdrawable shares.** [2](#0-1) 

`NodeDelegatorHelper.getWithdrawableShare` calls `DelegationManager.getWithdrawableShares`, which reflects post-slash balances. However, `stakedButUnverifiedNativeETH` remains at 32 ETH regardless of any beacon-chain slash that occurred before verification. [3](#0-2) 

**Step 3 – The inflated value propagates to the oracle's TVL calculation.**

`getETHDistributionData` in `LRTDepositPool` calls `getEffectivePodShares()` for every NDC: [4](#0-3) 

`_getTotalEthInProtocol` in `LRTOracle` calls `getTotalAssetDeposits`, which calls `getETHDistributionData`: [5](#0-4) 

**Step 4 – rsETH price is computed from the inflated TVL.** [6](#0-5) 

During the unverified window, the price is overstated by `slashed_amount / rsethSupply`, so new depositors receive fewer rsETH than they are entitled to.

**Step 5 – `verifyWithdrawalCredentials` always subtracts the full 32 ETH per validator.** [7](#0-6) 

After this call, `stakedButUnverifiedNativeETH` falls by 32 ETH while `withdrawableShare` rises by only `(32 - slashed) ETH`, producing a net decrease in `getEffectivePodShares` equal to the slashed amount.

**Step 6 – If the price drop exceeds `pricePercentageLimit`, the oracle auto-pauses the protocol.** [8](#0-7) 

Both `LRTDepositPool` and `LRTWithdrawalManager` are paused, freezing all deposits and withdrawals until an admin manually unpauses.

## Impact Explanation

**Medium – Temporary freezing of funds.** All user deposits and withdrawals are blocked for an indefinite period after the oracle auto-pause. The pause is triggered by a public call to `updateRSETHPrice()` once `verifyWithdrawalCredentials` has been called by the operator (a normal operational step). An admin holding `onlyLRTAdmin` must call `unpause()` on each contract before normal operation resumes. Additionally, during the unverified window, new depositors silently receive fewer rsETH than they are owed (Low: contract fails to deliver promised returns).

## Likelihood Explanation

Beacon-chain slashing is a known, documented risk for ETH validators. The vulnerable window exists between `stake32Eth` and `verifyWithdrawalCredentials`, which can span days to weeks in practice. The operator calling `verifyWithdrawalCredentials` is a mandatory operational step with no alternative. Once the price drop is baked in, any unprivileged user can call `updateRSETHPrice()` to trigger the auto-pause. A single slashed validator among the protocol's validator set is sufficient to trigger the pause path once credentials are verified, provided the slashed amount relative to total TVL exceeds `pricePercentageLimit`.

## Recommendation

1. **Track actual beacon-chain balance at verification time.** When `verifyWithdrawalCredentials` is called, read the actual verified balance from the EigenPod proof data and subtract that value (not a hardcoded 32 ETH) from `stakedButUnverifiedNativeETH`. Any shortfall should be recorded separately so the oracle can reflect the real TVL immediately.

2. **Decouple slashing-induced price drops from the auto-pause.** Consider distinguishing between organic price decreases (slashing) and manipulation-driven ones before triggering a full protocol pause, or widen `pricePercentageLimit` to accommodate expected slashing magnitudes.

3. **Add a slashing-reserve mechanism.** Maintain a reserve fund that can absorb small slashing events without causing a price drop that triggers the pause.

## Proof of Concept

1. Protocol stakes 32 ETH via `stake32Eth` → `stakedButUnverifiedNativeETH = 32 ETH`.
2. Beacon-chain slashes the validator by 1 ETH; actual balance = 31 ETH.
3. `getEffectivePodShares()` = 32 ETH (overstated). rsETH price is inflated. New depositors receive fewer rsETH.
4. Operator calls `verifyWithdrawalCredentials` → `stakedButUnverifiedNativeETH -= 32 ETH`; EigenLayer records 31 ETH as withdrawable shares.
5. `getEffectivePodShares()` drops by 1 ETH. `_getTotalEthInProtocol()` drops by 1 ETH.
6. Anyone calls `updateRSETHPrice()`. `newRsETHPrice < highestRsethPrice`. If `diff > pricePercentageLimit * highestRsethPrice`, the oracle calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`.
7. All user deposits and withdrawals are frozen until admin intervention.

**Foundry fork test plan:** Deploy against a mainnet fork with EigenLayer. Stake 32 ETH via `stake32Eth`. Simulate a beacon-chain slash by manipulating the EigenPod's recorded balance to 31 ETH (or use a mock EigenPod). Call `verifyWithdrawalCredentials`. Assert `getEffectivePodShares()` returns 31 ETH. Assert `updateRSETHPrice()` triggers the pause when the 1 ETH drop exceeds `pricePercentageLimit * highestRsethPrice`.

### Citations

**File:** contracts/NodeDelegator.sol (L165-168)
```text
        // tracks staked but unverified native ETH
        stakedButUnverifiedNativeETH += 32 ether;

        _getEigenPodManager().stake{ value: 32 ether }(pubkey, signature, depositDataRoot);
```

**File:** contracts/NodeDelegator.sol (L235-244)
```text
        if (stakedButUnverifiedNativeETH < validatorFields.length * (32 ether)) {
            revert InsufficientStakedBalance();
        }

        // reduce the eth amount that is verified
        stakedButUnverifiedNativeETH -= (validatorFields.length * (32 ether));

        eigenPod.verifyWithdrawalCredentials(
            beaconTimestamp, stateRootProof, validatorIndices, validatorFieldsProofs, validatorFields
        );
```

**File:** contracts/NodeDelegator.sol (L556-562)
```text
    function getEffectivePodShares() external view override returns (uint256 ethStaked) {
        uint256 withdrawableShare =
            NodeDelegatorHelper.getWithdrawableShare(lrtConfig, IStrategy(lrtConfig.beaconChainETHStrategy()));

        // staker balances can no longer be negative
        return stakedButUnverifiedNativeETH + withdrawableShare;
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

**File:** contracts/LRTDepositPool.sol (L487-489)
```text
            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
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
