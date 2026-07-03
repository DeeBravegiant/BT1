### Title
`assetsCommitted` exceeding `getTotalAssetDeposits` after EigenLayer slashing permanently blocks all new withdrawal requests — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`initiateWithdrawal` enforces that the expected asset payout does not exceed `getAvailableAssetAmount`, which is computed as `totalAssets − assetsCommitted`. When EigenLayer slashing reduces `totalAssets` below the already-committed amount, `getAvailableAssetAmount` returns 0 and every new withdrawal request reverts with `ExceedAmountToWithdraw`. This state arises naturally from protocol operation and blocks all users from queuing new withdrawals until operators manually drain the existing queue via `unlockQueue`.

---

### Finding Description

`initiateWithdrawal` in `LRTWithdrawalManager` performs the following sequence:

```
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
``` [1](#0-0) 

`getAvailableAssetAmount` is:

```solidity
uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
availableAssetAmount = totalAssets > assetsCommitted[asset]
    ? totalAssets - assetsCommitted[asset]
    : 0;
``` [2](#0-1) 

`getTotalAssetDeposits` aggregates assets across the deposit pool, NDCs, EigenLayer strategies, the converter, and the unstaking vault: [3](#0-2) 

`assetsCommitted[asset]` is incremented at withdrawal-request time using the oracle price at that moment and is only decremented when `unlockQueue` is called by an operator. EigenLayer slashing can reduce the on-chain asset balance (reflected in `getTotalAssetDeposits`) at any time, independently of `assetsCommitted`. Once `assetsCommitted > totalAssets`, `getAvailableAssetAmount` returns 0, and the strict `>` check at line 170 causes every subsequent `initiateWithdrawal` call to revert, regardless of how small the requested amount is. [4](#0-3) 

The block persists until an operator calls `unlockQueue`, which decrements `assetsCommitted` as it processes existing requests. If the unstaking vault is empty (assets are still queued in EigenLayer's delayed-withdrawal mechanism), `unlockQueue` itself reverts with `AmountMustBeGreaterThanZero`, extending the freeze further. [5](#0-4) 

---

### Impact Explanation

All unprivileged users are blocked from calling `initiateWithdrawal` for the affected asset. Their rsETH cannot be redeemed through the normal withdrawal path for the duration of the freeze. This constitutes **temporary freezing of funds** (Medium).

---

### Likelihood Explanation

EigenLayer slashing is an explicitly documented risk of restaking. The protocol delegates to multiple node operators via `NodeDelegator`, and any single slashing event that reduces `getTotalAssetDeposits` below the current `assetsCommitted` value triggers the freeze. Given that `assetsCommitted` can approach `totalAssets` under normal high-demand conditions, even a modest slashing event is sufficient to cross the threshold.

---

### Recommendation

Mirror the fix described in the referenced report: allow `initiateWithdrawal` to proceed even when `assetsCommitted ≥ totalAssets`, either by:

1. Removing the strict availability check and instead letting `unlockQueue` settle payouts at the prevailing price (already capped by `_calculatePayoutAmount`), or
2. Allowing users to commit to a proportionally reduced amount when the protocol is in an over-committed state, so new requests can still be queued without worsening the shortfall.

---

### Proof of Concept

1. `totalAssets(ETH)` = 100 ETH; `assetsCommitted[ETH]` = 0.
2. Users call `initiateWithdrawal` until `assetsCommitted[ETH]` = 95 ETH; `getAvailableAssetAmount` = 5 ETH.
3. EigenLayer slashing reduces the NDC's strategy balance by 10 ETH; `getTotalAssetDeposits(ETH)` drops to 90 ETH.
4. `getAvailableAssetAmount` = `max(0, 90 − 95)` = **0**.
5. Any user calling `initiateWithdrawal` with any non-zero `rsETHUnstaked` receives `ExceedAmountToWithdraw`.
6. The freeze persists until an operator calls `unlockQueue` and the unstaking vault holds sufficient ETH — which itself requires the EigenLayer delayed-withdrawal period to complete. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
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
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L596-603)
```text
    /// @notice Calculates the amount of asset available for withdrawal.
    /// @param asset The asset address.
    /// @return availableAssetAmount The asset amount avaialble for withdrawal.
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
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
