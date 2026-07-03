### Title
Stale `rsETHPrice` Cache Allows Depositors to Steal Accrued Yield Before Price Update - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle` stores `rsETHPrice` as a cached state variable updated only when `updateRSETHPrice()` is explicitly called. Because deposits use this stale cached price while `getAssetPrice()` reads live Chainlink values, an attacker can deposit at the stale (lower) price, trigger the price update themselves, and withdraw at the higher price — stealing yield that belongs to existing rsETH holders.

### Finding Description
`LRTOracle.rsETHPrice` is a stored value that only changes when `updateRSETHPrice()` is called. That function is `public whenNotPaused` — callable by any unprivileged address. [1](#0-0) 

The deposit mint formula in `LRTDepositPool.getRsETHAmountToMint()` is:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

`getAssetPrice(asset)` reads a **live** Chainlink price, while `rsETHPrice` is the **stale cached** value. As EigenLayer rewards accrue between `updateRSETHPrice()` calls, the true rsETH value rises above `rsETHPrice`. During this window, the denominator is artificially low, so depositors receive more rsETH than fair value.

`_updateRsETHPrice()` computes the true price from live Chainlink data via `_getTotalEthInProtocol()`: [3](#0-2) 

The withdrawal path in `LRTWithdrawalManager.getExpectedAssetAmount()` uses the same `rsETHPrice`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

After the attacker triggers `updateRSETHPrice()`, the stored price rises to reflect accrued rewards, and the attacker's withdrawal is calculated at the higher rate — returning more assets than were deposited.

### Impact Explanation
**High — Theft of unclaimed yield.** The attacker's excess rsETH dilutes all existing holders' proportional claim on the protocol TVL. The profit is extracted directly from yield that should have accrued to existing depositors. The attack is repeatable every reward cycle.

### Likelihood Explanation
**Medium.** EigenLayer rewards accrue continuously, so a staleness window exists between every pair of `updateRSETHPrice()` calls. No special permissions are required: `updateRSETHPrice()` is public, deposits are open, and the attacker controls the timing of both the deposit and the price update. The `pricePercentageLimit` guard limits per-call profit but does not prevent the attack — it only caps the price jump per invocation. [5](#0-4) 

### Recommendation
Call `_updateRsETHPrice()` (or an equivalent internal price refresh) atomically at the start of `depositAsset()` and `depositETH()` before computing `rsethAmountToMint`. This ensures every deposit uses the current, reward-inclusive price and eliminates the staleness window.

### Proof of Concept

**Initial state:**
- `rsETHPrice = 1.000 ETH` (stale; true value is `1.010 ETH` due to accrued EigenLayer rewards)
- stETH/ETH Chainlink price = `1.000`

**Step 1 — Attacker deposits 100 stETH at stale price:**
```
rsETH_minted = (100e18 * 1.000e18) / 1.000e18 = 100 rsETH
Fair amount  = (100e18 * 1.000e18) / 1.010e18 ≈  99.01 rsETH
Excess rsETH = 0.99 rsETH
``` [6](#0-5) 

**Step 2 — Attacker calls `updateRSETHPrice()`:**
- `rsETHPrice` updates to `1.010 ETH`

**Step 3 — Attacker calls `initiateWithdrawal(stETH, 100 rsETH)`:**
```
expectedAssetAmount = 100e18 * 1.010e18 / 1.000e18 = 101 stETH
``` [7](#0-6) 

**Step 4 — After `withdrawalDelayBlocks`, operator calls `unlockQueue`, attacker calls `completeWithdrawal`:**
- Attacker receives **101 stETH**
- Net profit: **1 stETH** extracted from existing holders' accrued yield [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-250)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
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
