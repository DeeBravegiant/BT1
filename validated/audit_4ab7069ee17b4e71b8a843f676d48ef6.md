### Title
Zero-Balance Validator Verification Post-Electra Causes TVL Undercount and rsETH Holder Dilution - (File: contracts/NodeDelegator.sol)

---

### Summary

`NodeDelegator.verifyWithdrawalCredentials()` unconditionally decrements `stakedButUnverifiedNativeETH` by 32 ETH per validator without checking whether the validator's effective balance is non-zero. After the Electra upgrade introduces pending balance deposits, a newly-registered validator can temporarily have a zero effective balance. Verifying such a validator causes EigenPod to award zero shares while the protocol's internal accounting permanently removes 32 ETH from `stakedButUnverifiedNativeETH`, creating a gap that propagates into the rsETH exchange rate calculation and dilutes existing rsETH holders.

---

### Finding Description

`NodeDelegator.verifyWithdrawalCredentials()` performs two actions in sequence:

1. Decrements `stakedButUnverifiedNativeETH` by `validatorFields.length * 32 ether` [1](#0-0) 
2. Delegates to `eigenPod.verifyWithdrawalCredentials()` [2](#0-1) 

There is no guard checking that the validator's effective balance is greater than zero before proceeding. [3](#0-2) 

Post-Electra (EIP-7251), a freshly deposited validator is registered on the beacon chain with **zero effective balance**; the actual 32 ETH balance is credited only after the pending balance deposit is processed (one epoch or more later). If the operator calls `verifyWithdrawalCredentials` during this window, EigenPod awards **zero shares** (because effective balance = 0), yet `stakedButUnverifiedNativeETH` has already been reduced by 32 ETH.

`getEffectivePodShares()` sums both components:

```solidity
return stakedButUnverifiedNativeETH + withdrawableShare;
``` [4](#0-3) 

After the bad verification: `stakedButUnverifiedNativeETH` = 0 (decremented), `withdrawableShare` = 0 (no shares awarded) → `getEffectivePodShares()` = **0** instead of 32 ETH.

`getETHDistributionData()` aggregates this across all NDCs:

```solidity
ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
``` [5](#0-4) 

This feeds directly into `_getTotalEthInProtocol()` inside `LRTOracle`, which drives the rsETH price calculation:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [6](#0-5) 

The 32 ETH gap causes `totalETHInProtocol` to be understated, lowering `newRsETHPrice`. Any depositor who calls `depositETH` or `depositAsset` while the price is suppressed receives more rsETH than the underlying value warrants, at the expense of all existing rsETH holders.

If `startCheckpoint` is subsequently called, EigenPod marks the zero-balance validator as **WITHDRAWN**, making the accounting gap permanent until the validator exits the beacon chain and the ETH is recovered through the withdrawal path.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every rsETH minted during the suppressed-price window is backed by less ETH than it should be. Existing holders' proportional claim on the protocol's ETH is permanently diluted by the excess rsETH issued to new depositors. The magnitude scales with the number of affected validators and the volume of deposits during the window. For a protocol holding thousands of ETH, even a handful of zero-balance validators can create a measurable and exploitable price gap.

---

### Likelihood Explanation

**Medium.**

- The Electra upgrade is a scheduled, publicly known network upgrade.
- The operator role (`onlyLRTOperator`) is a routine operational account that regularly calls `verifyWithdrawalCredentials` as part of normal validator onboarding.
- The operator has no on-chain signal that a validator's effective balance is still zero; the pending balance deposit window is invisible to the contract.
- No malicious intent is required — the operator acts in good faith following the standard onboarding flow.
- The window of vulnerability (one epoch or more per validator) is long enough for deposits to occur.

---

### Recommendation

Before decrementing `stakedButUnverifiedNativeETH` and calling `eigenPod.verifyWithdrawalCredentials()`, parse the `validatorFields` array and verify that each validator's effective balance field is greater than zero. Revert if any validator has a zero effective balance, mirroring the recommendation from the EigenLayer audit report. This prevents the accounting gap from ever being created.

---

### Proof of Concept

**Setup (post-Electra):**
1. Operator calls `stake32Eth(pubkey, sig, root)` → `stakedButUnverifiedNativeETH = 32 ether`. [7](#0-6) 
2. Beacon chain registers the validator with **effective balance = 0** (pending balance deposit not yet processed).

**Trigger:**
3. Operator calls `verifyWithdrawalCredentials(...)` for this validator.
   - Line 240: `stakedButUnverifiedNativeETH -= 32 ether` → now **0**. [8](#0-7) 
   - EigenPod awards **0 shares** (effective balance = 0).

**Accounting gap:**
4. `getEffectivePodShares()` returns `0 + 0 = 0` instead of 32 ETH. [4](#0-3) 
5. `getETHDistributionData()` undercounts `ethStakedInEigenLayer` by 32 ETH. [5](#0-4) 
6. `_getTotalEthInProtocol()` returns `X - 32 ETH`. [9](#0-8) 
7. `_updateRsETHPrice()` computes `newRsETHPrice = (X - 32 ETH) / rsethSupply` — price is suppressed. [6](#0-5) 

**Exploit:**
8. Any user calls `depositETH` or `depositAsset` while price is suppressed → receives excess rsETH minted at the artificially low rate, diluting all existing holders. [10](#0-9) 

**Escalation:**
9. Operator calls `startCheckpoint` → validator marked **WITHDRAWN** by EigenPod → gap becomes permanent until validator exits beacon chain and ETH is recovered. [11](#0-10)

### Citations

**File:** contracts/NodeDelegator.sol (L165-166)
```text
        // tracks staked but unverified native ETH
        stakedButUnverifiedNativeETH += 32 ether;
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

**File:** contracts/NodeDelegator.sol (L259-261)
```text
    function startCheckpoint(bool revertIfNoBalance) external onlyLRTOperator {
        eigenPod.startCheckpoint(revertIfNoBalance);
    }
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

**File:** contracts/LRTDepositPool.sol (L487-487)
```text
            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
