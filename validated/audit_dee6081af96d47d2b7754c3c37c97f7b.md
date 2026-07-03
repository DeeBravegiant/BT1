### Title
Attacker Can Exhaust `assetsCommitted` to DoS `initiateWithdrawal` for All Users - (`contracts/LRTWithdrawalManager.sol`)

### Summary

An attacker holding sufficient rsETH can submit enough `initiateWithdrawal` requests to inflate `assetsCommitted[asset]` to equal `getTotalAssetDeposits(asset)`, causing `getAvailableAssetAmount` to return 0 and blocking all subsequent `initiateWithdrawal` calls with `ExceedAmountToWithdraw` until operators call `unlockQueue` after the withdrawal delay.

### Finding Description

`initiateWithdrawal` in `LRTWithdrawalManager` has no per-user cap on total committed amount and no global rate limit. Each call:

1. Transfers rsETH from the caller into the contract.
2. Computes `expectedAssetAmount` via `getExpectedAssetAmount`.
3. Checks `expectedAssetAmount > getAvailableAssetAmount(asset)` — reverts if so.
4. Increments `assetsCommitted[asset] += expectedAssetAmount`. [1](#0-0) 

`getAvailableAssetAmount` returns:

```
totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0
``` [2](#0-1) 

where `totalAssets = lrtDepositPool.getTotalAssetDeposits(asset)` is a live read of all protocol assets. [3](#0-2) 

Once `assetsCommitted[asset] >= totalAssets`, `getAvailableAssetAmount` returns 0, and every subsequent `initiateWithdrawal` call reverts at line 170 with `ExceedAmountToWithdraw`. [4](#0-3) 

`assetsCommitted` is only decremented inside `_unlockWithdrawalRequests`, which is called by `unlockQueue` — a privileged function (`onlyAssetTransferOrOperatorRole`) that also requires the withdrawal delay (default 8 days) to have elapsed per request. [5](#0-4) 

### Impact Explanation

All users are blocked from calling `initiateWithdrawal` for the affected asset until operators process the attacker's queued requests via `unlockQueue` after the withdrawal delay. The freeze duration is bounded by the withdrawal delay (up to 8 days by default, up to 16 days maximum). [6](#0-5) 

The attacker's rsETH is locked in the contract but not lost; they will eventually receive their assets when their requests are unlocked.

### Likelihood Explanation

The attacker must hold rsETH such that:

```
rsETHUnstaked * rsETHPrice / assetPrice ≈ getTotalAssetDeposits(asset)
``` [7](#0-6) 

Since rsETH is priced close to its underlying basket, the attacker needs rsETH roughly equal in value to all protocol deposits of the targeted asset. For a mature protocol this is an enormous capital requirement, making the attack expensive and capital-inefficient. However, for smaller assets or early-stage deployments the threshold is reachable. There is no per-user withdrawal request limit or rate limit to prevent this.

### Recommendation

1. **Add a per-user cap** on total `assetsCommitted` per asset, or a maximum number of pending withdrawal requests per user.
2. **Add a global rate limit** on how much `assetsCommitted` can grow per block or per time window.
3. Alternatively, **reserve a minimum fraction** of `totalAssets` that can never be committed, ensuring `getAvailableAssetAmount` never reaches zero from user requests alone.

### Proof of Concept

```solidity
// Pseudocode unit test (Foundry)
function test_exhaustAssetsCommitted() public {
    uint256 totalDeposits = depositPool.getTotalAssetDeposits(stETH);
    // Attacker holds rsETH worth totalDeposits in stETH
    // minRsEthAmountToWithdraw defaults to 0, so any amount works
    uint256 rsETHNeeded = totalDeposits * oracle.getAssetPrice(stETH) / oracle.rsETHPrice();
    deal(address(rsETH), attacker, rsETHNeeded);

    vm.startPrank(attacker);
    rsETH.approve(address(withdrawalManager), rsETHNeeded);
    // Single large request commits all available assets
    withdrawalManager.initiateWithdrawal(stETH, rsETHNeeded, "");
    vm.stopPrank();

    // getAvailableAssetAmount now returns 0
    assertEq(withdrawalManager.getAvailableAssetAmount(stETH), 0);

    // Legitimate user cannot initiate withdrawal
    vm.prank(legitimateUser);
    vm.expectRevert(ILRTWithdrawalManager.ExceedAmountToWithdraw.selector);
    withdrawalManager.initiateWithdrawal(stETH, minAmount, "");
}
``` [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-178)
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
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L338-343)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L800-815)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
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
