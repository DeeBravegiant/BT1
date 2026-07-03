Audit Report

## Title
Permanent `stakedButUnverifiedNativeETH` Inflation When Validator Is Slashed Before Credential Verification — (`contracts/NodeDelegator.sol`)

## Summary

`stakedButUnverifiedNativeETH` is incremented by 32 ETH on every `stake32Eth` call and is decremented **only** inside `verifyWithdrawalCredentials`. If a validator is slashed on the beacon chain and exits before `verifyWithdrawalCredentials` is called, the EigenPod permanently rejects credential verification for that validator (exit epoch already set), leaving `stakedButUnverifiedNativeETH` stuck at 32 ETH with no on-chain correction path. `getEffectivePodShares` then double-counts the phantom 32 ETH alongside the real swept balance, inflating `rsETHPrice` and enabling existing rsETH holders to redeem at an inflated rate, draining ETH that does not exist in the protocol.

## Finding Description

**Increment — `stake32Eth`:**
Every call unconditionally adds 32 ETH to the counter. [1](#0-0) 

**Sole decrement — `verifyWithdrawalCredentials`:**
The counter is reduced by `validatorFields.length * 32 ether`. This is the only write path that decreases `stakedButUnverifiedNativeETH` in the entire contract. [2](#0-1) 

**EigenPod rejects verification for exited validators:**
The NatSpec copied directly into `NodeDelegator.verifyWithdrawalCredentials` states: *"Validators proven via this method MUST NOT have an exit epoch set already."* A slashed validator is forcibly exited by the beacon chain; once its exit epoch is set, `eigenPod.verifyWithdrawalCredentials` reverts, making it permanently impossible to decrement `stakedButUnverifiedNativeETH` for that validator. [3](#0-2) 

**No admin correction path:**
A grep across the entire contract confirms `stakedButUnverifiedNativeETH` is written in exactly two places: the increment in `stake32Eth` and the decrement in `verifyWithdrawalCredentials`. There is no setter, emergency override, or governance function to manually correct the value. [4](#0-3) 

**`getEffectivePodShares` sums both terms:**
After the slashed validator exits and its remaining ETH (e.g., 28 ETH after penalties) is swept to the EigenPod, running a checkpoint credits that ETH as `withdrawableShare`. At that point `getEffectivePodShares` returns `32 (phantom) + 28 (real) = 60 ETH` while actual recoverable ETH is 28 ETH — a permanent 32 ETH overcount. [5](#0-4) 

**TVL and price inflation path:**
`getEffectivePodShares` feeds `getETHDistributionData`: [6](#0-5) 

`getETHDistributionData` is returned by `getTotalAssetDeposits` for ETH, which is consumed by `_getTotalEthInProtocol`: [7](#0-6) 

Which drives `_updateRsETHPrice` and sets `rsETHPrice`: [8](#0-7) [9](#0-8) 

**`pricePercentageLimit` does not block the inflated price:**
The upside guard reverts for non-manager callers only if the increase exceeds the threshold; a manager can bypass it unconditionally (L263). For small TVL ratios the increase may be within the threshold entirely. Either way, the downside guard only pauses on price *decreases* and provides no protection against the inflated price being used for redemptions. [10](#0-9) 

## Impact Explanation

**Critical — Protocol insolvency / direct theft of user funds.**

Each slashed-before-verification validator permanently inflates `getEffectivePodShares` by 32 ETH. This inflates `totalETHInProtocol`, which inflates `rsETHPrice`. Existing rsETH holders can redeem at the inflated price, receiving more ETH than the protocol actually holds. The shortfall is borne by remaining depositors, constituting direct theft of at-rest user funds and eventual protocol insolvency. The inflation is permanent with no on-chain correction path.

## Likelihood Explanation

Beacon chain slashing has occurred on mainnet (e.g., correlation penalties during client bugs). No attacker action is required to trigger the slash — the attacker only needs to hold rsETH and observe the inflated price, then redeem. The vulnerable window (between `stake32Eth` and `verifyWithdrawalCredentials`) has no on-chain time constraint. The bug is permanent once triggered, so even a single slashing event creates a lasting accounting error that can be exploited at any time thereafter.

## Recommendation

1. Add an operator/admin function to manually decrease `stakedButUnverifiedNativeETH` for validators that can be proven to have exited without credential verification (e.g., by submitting a beacon chain proof of the validator's exit epoch and slashing status).
2. Alternatively, track per-validator state (pubkey → 32 ETH entry) so that a validator whose exit can be proven on-chain can be individually removed from the unverified counter.
3. Consider a staleness check: if a pubkey has been in the unverified set beyond a configurable threshold, flag it for operator review and optionally exclude it from `getEffectivePodShares` until resolved.

## Proof of Concept

```
// Foundry fork test outline (Holesky or mainnet fork)
1. Fork with a live NodeDelegator + EigenPod.
2. operator calls stake32Eth(pubkey, sig, root)
   → stakedButUnverifiedNativeETH == 32 ether
3. Simulate beacon chain slash for `pubkey`:
   set slashed=true, exit_epoch < FAR_FUTURE_EPOCH in beacon state mock.
4. Advance beacon chain until validator fully exits;
   28 ETH swept to EigenPod (4 ETH lost to slashing penalties).
5. Assert eigenPod.verifyWithdrawalCredentials(...) reverts
   (exit epoch already set).
6. Assert stakedButUnverifiedNativeETH == 32 ether  // permanently stuck
7. operator calls startCheckpoint() + verifyCheckpointProofs()
   → withdrawableShare == 28 ether
8. Assert getEffectivePodShares() == 60 ether       // double-count
9. Assert actual recoverable ETH == 28 ether
10. Call LRTOracle.updateRSETHPrice()
    → rsETHPrice inflated proportionally
11. Existing rsETH holder redeems at inflated price,
    receives more ETH than fair share → protocol insolvency.
```

### Citations

**File:** contracts/NodeDelegator.sol (L46-47)
```text
    /// @dev Tracks the balance staked to validators and has yet to have the credentials verified with EigenLayer.
    uint256 public stakedButUnverifiedNativeETH;
```

**File:** contracts/NodeDelegator.sol (L165-168)
```text
        // tracks staked but unverified native ETH
        stakedButUnverifiedNativeETH += 32 ether;

        _getEigenPodManager().stake{ value: 32 ether }(pubkey, signature, depositDataRoot);
```

**File:** contracts/NodeDelegator.sol (L215-216)
```text
     * @dev Withdrawal credential proofs MUST NOT be older than `currentCheckpointTimestamp`.
     * @dev Validators proven via this method MUST NOT have an exit epoch set already.
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

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
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
