### Title
Inflated TVL from Unslashed Queued Withdrawal Shares Enables Withdrawal Queue Underfunding - (File: contracts/NodeDelegator.sol)

### Summary

`NodeDelegator.getAssetUnstaking()` reads raw `scaledShares` from EigenLayer's `getQueuedWithdrawals()` without applying the current post-queue slashing factor. When an operator is slashed after a withdrawal is queued, the returned shares overstate the ETH/LST that will actually arrive. This inflated value propagates into `getTotalAssetDeposits()`, `rsETHPrice`, and `getAvailableAssetAmount()`, allowing users to initiate more withdrawal commitments than the protocol can fulfill, causing a withdrawal queue backlog.

### Finding Description

`NodeDelegator.getAssetUnstaking()` computes the amount of each asset currently in EigenLayer's withdrawal queue:

```solidity
// NodeDelegator.sol lines 405-427
function getAssetUnstaking(address asset) external view returns (uint256 amount) {
    (IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
        _getDelegationManager().getQueuedWithdrawals(address(this));

    for (uint256 withdrawalIndex = 0; ...) {
        ...
        uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
        amount += strategyAsset == LRTConstants.ETH_TOKEN
            ? sharesToUnstake
            : strategy.sharesToUnderlyingView(sharesToUnstake);
    }
}
```

`getQueuedWithdrawals()` returns the `scaledShares` stored in the `Withdrawal` struct at queue time. Per EigenLayer's own documentation on `getSlashableSharesInQueue`: *"the actual slashable amount could be less than this value as this doesn't account for amounts that have already been slashed."* Withdrawals remain slashable during the entire delay period (8 days in this protocol). If the operator is slashed after `initiateUnstaking()` is called, the actual ETH/LST received on `completeUnstaking()` will be less than `sharesToUnstake`, but `getAssetUnstaking()` still returns the pre-slashing value.

By contrast, `getEffectivePodShares()` correctly calls `NodeDelegatorHelper.getWithdrawableShare()` → `DelegationManager.getWithdrawableShares()`, which applies the current slashing factor. The two accounting paths are therefore inconsistent: active stake is correctly post-slashing, queued-withdrawal stake is pre-slashing.

The inflated value propagates through the following call chain:

1. `LRTDepositPool.getETHDistributionData()` / `getAssetDistributionData()` — adds `getAssetUnstaking()` to `ethUnstakingFromEigenLayer` / `assetUnstakingFromEigenLayer`
2. `LRTDepositPool.getTotalAssetDeposits()` — sums all components including the inflated unstaking amount
3. `LRTOracle._getTotalEthInProtocol()` — uses `getTotalAssetDeposits()` to compute total ETH backing
4. `LRTOracle._updateRsETHPrice()` — computes `newRsETHPrice` from inflated total ETH → rsETH price is overstated
5. `LRTWithdrawalManager.getAvailableAssetAmount()` — uses `getTotalAssetDeposits()` to determine how many withdrawals can be initiated → inflated ceiling allows over-commitment

### Impact Explanation

**Withdrawal queue underfunding (Medium — temporary freezing of funds):**

`LRTWithdrawalManager.initiateWithdrawal()` checks:
```solidity
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

Because `getAvailableAssetAmount()` is inflated, users can commit to withdrawing more assets than the protocol will actually receive. When `unlockQueue()` is later called, it is bounded by `unstakingVault.balanceOf(asset)` (the real vault balance), not the inflated committed amount. If the vault is short (because the EigenLayer withdrawal completed with less ETH/LST due to slashing), queued user withdrawals cannot be processed and remain locked until additional assets arrive — a temporary freeze.

**Inflated rsETH price (Low — contract fails to deliver promised returns):**

New depositors calling `depositETH()` / `depositAsset()` receive fewer rsETH tokens than they should because `getRsETHAmountToMint()` divides by the inflated `rsETHPrice`. When the EigenLayer withdrawal eventually completes with less ETH, `updateRSETHPrice()` corrects the price downward, at which point existing rsETH holders have lost value relative to what the inflated price implied.

### Likelihood Explanation

EigenLayer's slashing model allows operators to be slashed at any time, including during the 8-day withdrawal delay window (`withdrawalDelayBlocks = 8 days / 12 seconds`). The Kelp DAO protocol delegates to external EigenLayer operators; any AVS slash against those operators during an active withdrawal window triggers this condition. The scenario is realistic and requires no privileged access — it is a normal consequence of restaking risk that the protocol's accounting does not handle correctly.

### Recommendation

In `getAssetUnstaking()`, apply the current slashing factor to the queued shares before converting them to an asset amount. For the beaconChainETHStrategy, query `EigenPodManager.beaconChainSlashingFactor()` and `DelegationManager.depositScalingFactor()` for the NDC address and multiply `sharesToUnstake` by the combined slashing factor before summing. For LST strategies, apply `DelegationManager.depositScalingFactor()` and the operator's current `maxMagnitude` similarly. Alternatively, use EigenLayer's `DelegationManager.getWithdrawableShares()` on the queued withdrawal's staker/strategies to obtain the already-adjusted withdrawable amount, consistent with how `getEffectivePodShares()` already works.

### Proof of Concept

1. NDC has 32 ETH staked in EigenLayer via `stake32Eth()` and credentials verified (`withdrawableShares = 32 ETH`).
2. Operator is slashed 50% by an AVS (`maxMagnitude = 0.5`); `getEffectivePodShares()` now correctly returns 16 ETH.
3. Operator calls `initiateUnstaking()` for the beaconChainETHStrategy with 32 deposit shares. EigenLayer stores `scaledShares = 32 ETH` in the `Withdrawal` struct.
4. Operator is slashed another 50% during the 8-day delay.
5. `getAssetUnstaking(ETH)` returns `sharesToUnstake = 32 ETH` (raw stored value), not the post-slashing ~8 ETH.
6. `getTotalAssetDeposits(ETH)` = `0 (pool) + 0 (NDC) + 0 (active EL) + 32 (unstaking) + 0 (vault)` = 32 ETH, inflated by ~24 ETH.
7. `rsETHPrice` is computed against this inflated 32 ETH TVL; new depositors receive fewer rsETH.
8. `getAvailableAssetAmount(ETH)` is inflated; users initiate withdrawal commitments totalling up to 32 ETH.
9. `completeUnstaking()` delivers only ~8 ETH to the vault.
10. `unlockQueue()` can only process ~8 ETH worth of withdrawals; the remaining committed withdrawals are frozen until more ETH arrives. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** contracts/LRTDepositPool.sol (L467-500)
```text
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;

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

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
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

**File:** contracts/LRTWithdrawalManager.sol (L162-177)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L837-851)
```text
    function _createUnlockParams(
        ILRTOracle lrtOracle,
        ILRTUnstakingVault unstakingVault,
        address asset
    )
        internal
        view
        returns (UnlockParams memory)
    {
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```
