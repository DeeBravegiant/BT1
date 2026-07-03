### Title
`stakedButUnverifiedNativeETH` Overstates TVL When a Validator Is Slashed Before Credential Verification — (File: `contracts/NodeDelegator.sol`)

---

### Summary

`getEffectivePodShares()` sums `stakedButUnverifiedNativeETH` (a fixed 32-ETH-per-validator counter) with the EigenLayer-tracked `withdrawableShare`. The counter is never adjusted for beacon-chain slashing that occurs before `verifyWithdrawalCredentials()` is called. This causes `getETHDistributionData()` to overstate the protocol's TVL, inflating the rsETH price and allowing early redeemers to extract more ETH than their fair share at the expense of remaining holders.

---

### Finding Description

**Lifecycle of `stakedButUnverifiedNativeETH`:**

`stake32Eth()` increments the counter by exactly 32 ETH per validator: [1](#0-0) 

`verifyWithdrawalCredentials()` decrements it by the same fixed amount, regardless of the validator's actual beacon-chain balance at proof time: [2](#0-1) 

`getEffectivePodShares()` adds the raw counter to the EigenLayer-tracked withdrawable share: [3](#0-2) 

`getETHDistributionData()` sums `getEffectivePodShares()` across all NDCs to produce the ETH TVL used by the oracle: [4](#0-3) 

**Where the question's mechanism is partially incorrect:**

The question states that slashing before credential verification *reduces `beaconChainSlashingFactor`*. This is not accurate. `beaconChainSlashingFactor` is a pod-level factor updated only when `recordBeaconChainETHBalanceUpdate` is called by the EigenPod during checkpoint completion. Checkpoints only include **ACTIVE** validators — i.e., those whose withdrawal credentials have already been verified. An unverified validator is invisible to EigenLayer; its slashing does not touch `beaconChainSlashingFactor` at all. [5](#0-4) 

The actual bug is simpler: `stakedButUnverifiedNativeETH` is a raw 32-ETH counter with no mechanism to reflect a beacon-chain balance decrease that occurs before credential verification. When a validator is slashed (e.g., from 32 ETH to 16 ETH) during this window, the counter still reads 32 ETH, overstating the recoverable ETH by the slashed amount.

**TVL inflation path:**

`_getTotalEthInProtocol()` in `LRTOracle` calls `getTotalAssetDeposits(ETH_TOKEN)` → `getETHDistributionData()` → `getEffectivePodShares()`. The inflated TVL feeds directly into `_updateRsETHPrice()`, raising the rsETH price above what is actually recoverable. [6](#0-5) 

---

### Impact Explanation

During the window between `stake32Eth()` and `verifyWithdrawalCredentials()`, if a validator is slashed on the beacon chain, the rsETH price is inflated by the unaccounted loss. Any rsETH holder who redeems during this window receives more ETH than their proportional share. The shortfall is socialised across remaining holders when the credential proof is eventually submitted and the actual (lower) balance is registered with EigenLayer. This is a direct, quantifiable transfer of value from remaining holders to early redeemers — **theft of unclaimed yield** (High).

---

### Likelihood Explanation

- Beacon-chain slashing events are rare but not impossible, especially for large operators running many validators.
- The unverified window can span days to weeks depending on operator cadence.
- The slashing event is public on the beacon chain; any observer can detect it and redeem rsETH before the operator calls `verifyWithdrawalCredentials()`.
- No privileged access is required; the attacker only needs to hold rsETH and initiate a withdrawal.
- Likelihood is **Low** in isolation, but the window is operator-controlled and can be extended inadvertently.

---

### Recommendation

When `verifyWithdrawalCredentials()` is called, read the validator's actual effective balance from the beacon-chain proof fields rather than subtracting a fixed 32 ETH. The difference between 32 ETH and the proven effective balance should be written off immediately (e.g., emitting a loss event and reducing a separate "pending" TVL figure). Alternatively, maintain a separate `pendingSlashAdjustment` variable that is updated off-chain and applied to `stakedButUnverifiedNativeETH` before it is included in TVL.

---

### Proof of Concept

```solidity
// Fork test (local fork, no mainnet interaction)
// 1. Deploy NDC, stake one validator → stakedButUnverifiedNativeETH = 32 ether
// 2. Simulate beacon-chain slash: validator effective balance drops to 16 ether
//    (no EigenLayer call occurs; beaconChainSlashingFactor is unchanged)
// 3. Call getEffectivePodShares() → returns 32 ether (overstated by 16 ether)
// 4. Call getETHDistributionData() → ethStakedInEigenLayer includes the phantom 16 ether
// 5. Call updateRSETHPrice() → rsETH price is inflated
// 6. Redeem rsETH at inflated price → receive more ETH than fair share
// 7. Call verifyWithdrawalCredentials() with actual balance proof (16 ether)
//    → stakedButUnverifiedNativeETH -= 32 ether, EigenLayer registers 16 ether
// 8. Call updateRSETHPrice() again → price drops, remaining holders bear the loss

// Assert: ETH extracted in step 6 > proportional share of actual recoverable ETH
```

### Citations

**File:** contracts/NodeDelegator.sol (L165-168)
```text
        // tracks staked but unverified native ETH
        stakedButUnverifiedNativeETH += 32 ether;

        _getEigenPodManager().stake{ value: 32 ether }(pubkey, signature, depositDataRoot);
```

**File:** contracts/NodeDelegator.sol (L239-244)
```text
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

**File:** contracts/LRTDepositPool.sol (L484-492)
```text
        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
```

**File:** contracts/external/eigenlayer/interfaces/IEigenPodManager.sol (L57-71)
```text
interface IEigenPodManagerTypes {
    /**
     * @notice The amount of beacon chain slashing experienced by a pod owner as a proportion of WAD
     * @param isSet whether the slashingFactor has ever been updated. Used to distinguish between
     * a value of "0" and an uninitialized value.
     * @param slashingFactor the proportion of the pod owner's balance that has been decreased due to
     * slashing or other beacon chain balance decreases.
     * @dev NOTE: if !isSet, `slashingFactor` should be treated as WAD. `slashingFactor` is monotonically
     * decreasing and can hit 0 if fully slashed.
     */
    struct BeaconChainSlashingFactor {
        bool isSet;
        uint64 slashingFactor;
    }
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
