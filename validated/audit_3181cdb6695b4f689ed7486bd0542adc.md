### Title
Stale `rsETHPrice` Read Without Prior `updateRSETHPrice()` Call in `initiateWithdrawal` Permanently Caps User Payout Below Fair Value - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.initiateWithdrawal()` reads `lrtOracle.rsETHPrice()` to compute and permanently store `expectedAssetAmount` in the withdrawal request without first calling `updateRSETHPrice()`. Because `_calculatePayoutAmount` later pays `min(expectedAssetAmount, currentReturn)`, a stale (lower) price at initiation time permanently caps the user's payout below fair value. There is no cancel-and-resubmit path in the contract.

### Finding Description
`initiateWithdrawal` calls `getExpectedAssetAmount`, which reads the stored `rsETHPrice` directly:

```solidity
// LRTWithdrawalManager.sol:593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

The result is written permanently into storage:

```solidity
// LRTWithdrawalManager.sol:751-753
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
});
```

When the operator later calls `unlockQueue`, `_calculatePayoutAmount` resolves the final disbursement as:

```solidity
// LRTWithdrawalManager.sol:833-834
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

`rsETHPrice` in `LRTOracle` is a stored variable updated only when `updateRSETHPrice()` / `_updateRsETHPrice()` is explicitly called. [1](#0-0)  Because rsETH continuously accrues restaking yield, the stored price drifts below the true current value between updates. `initiateWithdrawal` never calls `updateRSETHPrice()` before reading the price, so a user who submits a withdrawal request during a stale window receives a permanently lower `expectedAssetAmount`. [2](#0-1) 

The reference report's fix for the analogous `retain()` bug was to call `checkpointNominee` before reading the weight. The direct analog here is calling `updateRSETHPrice()` before reading `rsETHPrice`. `calculateStakingIncentives` in the reference codebase does exactly this; `initiateWithdrawal` does not.

There is no `cancelWithdrawal` function anywhere in `LRTWithdrawalManager`, so the user cannot undo the request and resubmit it after the price is refreshed. [3](#0-2) 

### Impact Explanation
The user permanently receives fewer underlying assets than their rsETH entitles them to. The shortfall is `rsETHUnstaked × (truePrice − stalePrice) / assetPrice`. This is a permanent loss of unclaimed yield — the yield that accrued between the last `updateRSETHPrice()` call and the `initiateWithdrawal` call — with no recovery path.

**Impact: Low** — Contract fails to deliver promised returns, but does not lose principal value.

### Likelihood Explanation
`rsETHPrice` is updated by the public `updateRSETHPrice()` function. [1](#0-0)  In practice the price can be stale for hours or days between keeper calls, or when the `pricePercentageLimit` guard causes the public call to revert for non-managers. [4](#0-3)  Any rsETH holder who calls `initiateWithdrawal` during such a window locks in the lower amount permanently. The function is permissionless for any rsETH holder, making this a routine user-reachable path.

### Recommendation
Call `updateRSETHPrice()` (or inline `_updateRsETHPrice()`) at the top of `initiateWithdrawal` before `getExpectedAssetAmount` is invoked, mirroring the pattern used in the reference report's `calculateStakingIncentives` fix:

```solidity
function initiateWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId)
    external nonReentrant whenNotPaused onlySupportedAsset(asset) onlySupportedStrategy(asset)
{
+   ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    ...
    uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
    ...
}
```

### Proof of Concept
1. `rsETHPrice` in `LRTOracle` is `1.05e18` (stale; true value is `1.10e18` after recent yield accrual).
2. User calls `initiateWithdrawal(stETH, 100e18, "")`.
3. `getExpectedAssetAmount` computes `100e18 × 1.05e18 / 1e18 = 105e18` and stores it. [5](#0-4) 
4. Operator calls `updateRSETHPrice()` — price updates to `1.10e18`.
5. Operator calls `unlockQueue`; `_calculatePayoutAmount` computes `currentReturn = 100e18 × 1.10e18 / 1e18 = 110e18`. [6](#0-5) 
6. Payout = `min(105e18, 110e18) = 105e18`.
7. User receives 105 stETH instead of 110 stETH — permanently losing 5 stETH of accrued yield with no recourse.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L260-266)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTWithdrawalManager.sol (L162-176)
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

```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-738)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;

        // If Aave integration is enabled and asset is ETH, withdraw from Aave if needed
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
            uint256 contractBalance = address(this).balance;
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
