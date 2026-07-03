### Title
Stale `rsETHPrice` Read in `initiateWithdrawal` and `instantWithdrawal` Without Prior `updateRSETHPrice()` Call - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.initiateWithdrawal()` and `instantWithdrawal()` both read `lrtOracle.rsETHPrice()` — a stored, manually-updated value — without first calling `updateRSETHPrice()`. This is the direct analog of the FraxlendPairCore pattern where `_totalAssetAvailable` is read without first calling `whitelistUpdate()` to refresh `vaultUtilization`. The result is that withdrawal amounts and asset-commitment accounting are computed against a potentially stale exchange rate.

### Finding Description

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called (public, callable by anyone): [1](#0-0) 

The value is written only inside `_updateRsETHPrice()`: [2](#0-1) 

`LRTWithdrawalManager.getExpectedAssetAmount()` reads this stored value directly: [3](#0-2) 

Both `initiateWithdrawal` and `instantWithdrawal` call `getExpectedAssetAmount` without first refreshing the price:

**`initiateWithdrawal`:** [4](#0-3) 

**`instantWithdrawal`:** [5](#0-4) 

### Impact Explanation

**Scenario A — stale price is lower than actual (normal operation: rewards have accrued since last update):**

- `initiateWithdrawal`: `expectedAssetAmount` is under-calculated → `assetsCommitted[asset]` is under-counted → `getAvailableAssetAmount` returns an inflated value → more users can queue withdrawals than the unstaking vault can actually service. When `unlockQueue` runs, `_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn) = expectedAssetAmount` (the stale-lower amount), so users are systematically underpaid relative to the rsETH they burned. Excess rsETH value is silently retained by the protocol. [6](#0-5) 

- `instantWithdrawal`: user burns rsETH and receives fewer assets than the current rate entitles them to. The contract fails to deliver promised returns.

**Scenario B — stale price is higher than actual (price has decreased, e.g. slashing, before `updateRSETHPrice()` is called):**

- `instantWithdrawal`: `assetAmountUnlocked` is over-calculated. The attacker burns rsETH and redeems more assets from the unstaking vault than the current rsETH/ETH rate justifies. The downside-protection pause in `_updateRsETHPrice()` is never triggered because `updateRSETHPrice()` is never called before the withdrawal executes. [7](#0-6) 

The check against `getAssetsAvailableForInstantWithdrawal` limits the per-transaction drain but does not prevent the rate mismatch. [8](#0-7) 

### Likelihood Explanation

`rsETHPrice` is updated by operators on a periodic schedule, not atomically before every withdrawal. Any user can call `updateRSETHPrice()` themselves, but nothing in `initiateWithdrawal` or `instantWithdrawal` enforces this. The window between operator updates (which can span hours) is sufficient for rewards to accrue and for the stored price to diverge meaningfully from the true rate. Likelihood is **Medium**.

### Recommendation

Call `updateRSETHPrice()` (or an internal equivalent) at the start of both `initiateWithdrawal` and `instantWithdrawal` before reading `lrtOracle.rsETHPrice()`, mirroring the pattern recommended in the reference report for `whitelistUpdate`:

```solidity
// At the top of initiateWithdrawal and instantWithdrawal:
ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
```

This ensures the price used for `expectedAssetAmount` and `assetAmountUnlocked` always reflects the latest protocol state.

### Proof of Concept

1. Rewards accrue in EigenLayer strategies; `rsETHPrice` stored in `LRTOracle` is 1.00 ETH but the true current rate is 1.05 ETH (nobody has called `updateRSETHPrice()` yet).
2. User calls `initiateWithdrawal(ETH, 100e18, ...)`.
3. `getExpectedAssetAmount` computes `100e18 * 1.00e18 / 1.00e18 = 100 ETH` instead of the correct `105 ETH`.
4. `assetsCommitted[ETH] += 100 ETH` (under-counted by 5 ETH).
5. `getAvailableAssetAmount` returns 5 ETH more than it should, allowing an additional withdrawal that would otherwise be blocked.
6. When `unlockQueue` is eventually called with the refreshed price, `_calculatePayoutAmount` returns `min(100, 105) = 100 ETH` — the user receives 100 ETH despite having burned rsETH worth 105 ETH at the current rate.
7. The 5 ETH shortfall is silently absorbed by the protocol rather than returned to the user. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
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
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

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

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
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

**File:** contracts/LRTWithdrawalManager.sol (L824-834)
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
```
