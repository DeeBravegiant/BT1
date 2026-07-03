### Title
Beacon-Chain Slash Before `verifyWithdrawalCredentials` Permanently Overstates TVL, Enabling Theft of Yield by Early Redeemers — (`contracts/NodeDelegator.sol`)

---

### Summary

`NodeDelegator.getEffectivePodShares()` returns `stakedButUnverifiedNativeETH + withdrawableShare`. The first term is decremented by exactly 32 ETH per validator only when `verifyWithdrawalCredentials` is called. If a validator is slashed on the beacon chain before that call, EigenLayer will award fewer than 32 ETH of shares, but `stakedButUnverifiedNativeETH` still carries the full 32 ETH. The resulting TVL overstatement propagates through `getETHDistributionData()` → `getTotalAssetDeposits()` → `_getTotalEthInProtocol()` → `updateRSETHPrice()`, inflating `rsETHPrice`. Any rsETH holder who redeems during this window extracts more ETH than the protocol actually controls, socialising the loss onto remaining holders.

---

### Finding Description

**`stake32Eth` always adds exactly 32 ETH:** [1](#0-0) 

**`verifyWithdrawalCredentials` always subtracts exactly 32 ETH, then delegates to EigenLayer which awards shares based on the validator's *effective balance at proof time*:** [2](#0-1) 

**`getEffectivePodShares` adds both terms:** [3](#0-2) 

**`getETHDistributionData` sums `getEffectivePodShares()` across all NDCs:** [4](#0-3) 

**`_getTotalEthInProtocol` calls `getTotalAssetDeposits` (which calls `getETHDistributionData` for ETH) and multiplies by the asset price:** [5](#0-4) 

**`updateRSETHPrice` is public and sets `rsETHPrice = totalETHInProtocol / rsethSupply`:** [6](#0-5) [7](#0-6) 

**The invariant break:** Between `stake32Eth` and `verifyWithdrawalCredentials`, `stakedButUnverifiedNativeETH` = 32 ETH and `withdrawableShare` = 0, so `getEffectivePodShares()` = 32 ETH. If the validator was slashed on the beacon chain during this window (e.g., effective balance = 16 ETH), the actual ETH controlled is 16 ETH, but the TVL reports 32 ETH — a 16 ETH overstatement. `rsETHPrice` is set to this inflated value. When `verifyWithdrawalCredentials` is eventually called, EigenLayer awards only ~16 ETH of shares, `stakedButUnverifiedNativeETH` drops to 0, and the TVL (and price) corrects downward. Any holder who redeemed during the inflated window extracted more ETH than the protocol backed.

There is no code-level ordering guarantee that forces `verifyWithdrawalCredentials` to be called before withdrawals are unlocked. The withdrawal unlock path in `LRTWithdrawalManager` uses the current `rsETHPrice` at unlock time: [8](#0-7) 

An attacker who monitors the beacon chain, detects a slash, and requests/receives a withdrawal unlock before the operator calls `verifyWithdrawalCredentials` redeems at the inflated price.

---

### Impact Explanation

The rsETH price invariant — that `rsETHPrice` must reflect only real backing assets — is violated. Early redeemers receive more ETH than they deposited (proportionally), while remaining holders bear the deficit when the price corrects. This is a direct, quantifiable theft of yield from other rsETH holders. The magnitude equals `(32 - effectiveBalanceAtSlash) * rsETHPrice / totalSupply` per slashed validator, multiplied by the number of rsETH redeemed during the window.

---

### Likelihood Explanation

- Beacon chain slashings are public and observable in real time.
- `updateRSETHPrice()` is callable by anyone — no role required.
- The gap between `stake32Eth` and `verifyWithdrawalCredentials` is an operational window that can span hours to days (proof generation, gas conditions, operator scheduling).
- No on-chain guard enforces that `verifyWithdrawalCredentials` must be called before any withdrawal is unlocked.
- The downside-protection pause in `_updateRsETHPrice` only triggers *after* the price corrects downward past `pricePercentageLimit`; it does not prevent redemptions during the inflated window. [9](#0-8) 

---

### Recommendation

1. **Remove `stakedButUnverifiedNativeETH` from the TVL calculation entirely**, or cap it at the EigenLayer-reported effective balance once a checkpoint is available.
2. **Alternatively**, when computing `getEffectivePodShares()`, query `eigenPod.validatorPubkeyToInfo()` (or equivalent) to sum only the `restakedBalanceGwei` of each unverified validator rather than assuming 32 ETH.
3. **Enforce ordering**: require that `verifyWithdrawalCredentials` is called (and the price updated) before any withdrawal unlock batch is processed, either via a contract-level check or a time-lock.
4. **Add a staleness guard**: if `stakedButUnverifiedNativeETH > 0` for longer than a configurable threshold, pause price updates or flag the discrepancy.

---

### Proof of Concept

```solidity
// Fork test outline (Foundry, local fork)
function test_slashBeforeVerify_inflatesPrice() public {
    // 1. Operator stakes 32 ETH via stake32Eth → stakedButUnverifiedNativeETH = 32e18
    nodeDelegator.stake32Eth{value: 32 ether}(pubkey, sig, root);

    // 2. Simulate beacon-chain slash: mock EigenPod to return 16e18 shares
    //    on verifyWithdrawalCredentials (effective balance = 16 ETH)
    vm.mockCall(
        address(eigenPod),
        abi.encodeWithSelector(IEigenPod.verifyWithdrawalCredentials.selector),
        abi.encode() // awards 16e18 shares internally via recordBeaconChainETHBalanceUpdate
    );

    // 3. Before verification: getEffectivePodShares() = 32e18 (inflated)
    uint256 podShares = nodeDelegator.getEffectivePodShares();
    assertEq(podShares, 32 ether); // overstated by 16 ETH

    // 4. updateRSETHPrice() sets inflated price
    lrtOracle.updateRSETHPrice();
    uint256 inflatedPrice = lrtOracle.rsETHPrice();

    // 5. Attacker redeems rsETH at inflated price
    // (withdrawal unlock uses inflatedPrice)
    uint256 ethOut = attackerRsETHBalance * inflatedPrice / 1e18;
    // ethOut > actual ETH deposited by attacker

    // 6. Operator calls verifyWithdrawalCredentials → awards 16e18 shares
    nodeDelegator.verifyWithdrawalCredentials(...);

    // 7. Price corrects downward
    lrtOracle.updateRSETHPrice();
    uint256 correctedPrice = lrtOracle.rsETHPrice();
    assertLt(correctedPrice, inflatedPrice);

    // 8. Remaining holders bear the 16 ETH deficit
    // Assert: attacker extracted more ETH than deposited
    assertGt(ethOut, attackerInitialDeposit);
}
```

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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

**File:** contracts/LRTOracle.sol (L331-348)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L283-303)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );
```
