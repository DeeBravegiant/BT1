### Title
`getETHDistributionData` Omits ETH Held in `LRTWithdrawalManager` (Idle and Aave-Deposited), Causing Understated Protocol TVL and Deflated rsETH Price - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.getETHDistributionData()` does not account for ETH that has been transferred from `LRTUnstakingVault` into `LRTWithdrawalManager` during `unlockQueue` execution — whether sitting idle in the withdrawal manager or deposited into Aave. This causes `getTotalAssetDeposits(ETH)` and, transitively, `LRTOracle._getTotalEthInProtocol()` to undercount the protocol's true ETH TVL after every ETH withdrawal unlock cycle, deflating the computed rsETH price.

---

### Finding Description

`getETHDistributionData()` enumerates ETH across five locations:

```
ethLyingInDepositPool  = address(this).balance
ethLyingInNDCs         = sum of NDC.balance
ethStakedInEigenLayer  = sum of NDC.getEffectivePodShares()
ethUnstakingFromEigenLayer = sum of NDC.getAssetUnstaking(ETH)
ethLyingInUnstakingVault   = lrtUnstakingVault.balance
ethLyingInConverter        = ILRTConverter.ethValueInWithdrawal()
``` [1](#0-0) 

When `LRTWithdrawalManager.unlockQueue()` is called for ETH, it executes:

```solidity
unstakingVault.redeem(asset, assetAmountUnlocked);   // ETH leaves vault → enters WithdrawalManager
// if Aave enabled:
this.depositToAaveExternal(assetAmountUnlocked);      // ETH leaves WithdrawalManager → enters Aave
``` [2](#0-1) 

After this transfer, the ETH is no longer in `lrtUnstakingVault.balance`, so `ethLyingInUnstakingVault` decreases. But `getETHDistributionData()` has no term for:
- `address(lrtWithdrawalManager).balance` (idle ETH awaiting user completion), nor
- `aaveAWETH.balanceOf(lrtWithdrawalManager)` (ETH deposited to Aave from the withdrawal manager). [3](#0-2) 

`getTotalAssetDeposits(ETH)` sums all six fields from `getETHDistributionData()` and is called by `LRTOracle._getTotalEthInProtocol()` to compute the rsETH price:

```solidity
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [4](#0-3) 

The rsETH price is then:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [5](#0-4) 

Every time `unlockQueue` moves ETH out of the unstaking vault, the reported TVL drops by exactly `assetAmountUnlocked`, deflating `newRsETHPrice` below its true value.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds / incorrect rsETH price**

Two concrete consequences:

1. **Protocol self-pause**: `_updateRsETHPrice()` compares `newRsETHPrice` against `highestRsethPrice`. If the artificial price drop exceeds `pricePercentageLimit`, the function pauses `LRTDepositPool` and `LRTWithdrawalManager`, freezing all deposits and withdrawals until an admin manually unpauses. [6](#0-5) 

2. **Dilution of existing rsETH holders**: A deflated rsETH price means new depositors receive more rsETH per ETH deposited than they should, diluting the share value of existing holders — a form of yield theft. [7](#0-6) 

---

### Likelihood Explanation

**Likelihood: High**

`unlockQueue` is a routine operational call executed by operators on every withdrawal cycle. Every ETH unlock event moves ETH from the unstaking vault into the withdrawal manager, triggering the accounting gap. The gap persists until users call `completeWithdrawal`, which drains the withdrawal manager. During the window between `unlockQueue` and `completeWithdrawal` (which can span the 8-day withdrawal delay), the TVL undercount is active. [8](#0-7) 

---

### Recommendation

Add the ETH held in `LRTWithdrawalManager` (both idle balance and Aave-deposited principal) to `getETHDistributionData()`. Concretely, introduce a new return field or add to `ethLyingInUnstakingVault`:

```solidity
address lrtWithdrawalManager = lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER);
ethLyingInWithdrawalManager = lrtWithdrawalManager.balance
    + ILRTWithdrawalManager(lrtWithdrawalManager).totalETHDepositedToAave();
```

This mirrors the existing pattern for `ethLyingInUnstakingVault` and `ethLyingInConverter`, ensuring all ETH locations are enumerated. [9](#0-8) 

---

### Proof of Concept

1. Protocol has 1000 ETH total: 900 in EigenPod, 100 in `LRTUnstakingVault`. rsETH supply = 1000, price = 1.0 ETH.
2. Operator calls `unlockQueue(ETH, ...)` to unlock 100 ETH for pending withdrawals.
3. `unstakingVault.redeem(ETH, 100)` moves 100 ETH from vault to `LRTWithdrawalManager`. If Aave enabled, it is deposited to Aave.
4. `getETHDistributionData()` now returns: `ethLyingInUnstakingVault = 0`, no term for the 100 ETH in the withdrawal manager.
5. `getTotalAssetDeposits(ETH)` = 900 (instead of 1000).
6. `_getTotalEthInProtocol()` = 900.
7. `newRsETHPrice = 900 / 1000 = 0.9 ETH` — a 10% artificial drop.
8. If `pricePercentageLimit` ≤ 10%, `_updateRsETHPrice()` pauses `LRTDepositPool` and `LRTWithdrawalManager`, freezing all user activity. [6](#0-5) [10](#0-9)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L268-320)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
    {
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

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);

        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }

        emit AssetUnlocked(asset, rsETHBurned, assetAmountUnlocked, params.rsETHPrice, params.assetPrice);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L893-901)
```text
    /// @param amount The amount of ETH to deposit
    function _depositToAave(uint256 amount) internal {
        if (amount == 0) return;

        aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
        totalETHDepositedToAave += amount;

        emit ETHDepositedToAave(amount, totalETHDepositedToAave);
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

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

**File:** contracts/LRTOracle.sol (L336-348)
```text
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
