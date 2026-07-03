### Title
No On-Chain Token Migration Mechanism Causes Permanent Fund Freeze When Supported LST Migrates - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.removeSupportedAsset` enforces a hard guard that prevents removal of any supported asset that still holds deposits. If a supported LST (stETH, ETHx) migrates to a new contract address, the protocol cannot remove the old asset, cannot migrate existing holdings to the new address, and users' rsETH claims become permanently tied to the deprecated old token — mirroring the Balancer BToken.sol token-migration freeze.

---

### Finding Description

`LRTConfig.removeSupportedAsset` contains the following guard: [1](#0-0) 

```solidity
if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
    revert CannotRemoveAssetWithDeposits(asset);
}
```

This means that as long as any meaningful balance of the old token exists anywhere in the protocol (DepositPool, NDCs, EigenLayer strategies, UnstakingVault), the old address cannot be removed from `supportedAssetList` or `isSupportedAsset`. [2](#0-1) 

The protocol has no on-chain migration function that would:
- Swap old-token holdings for new-token holdings atomically, or
- Remap `assetStrategy`, `depositLimitByAsset`, and `isSupportedAsset` from the old address to the new address.

The withdrawal manager locks users into specific asset addresses at request time: [3](#0-2) 

Once a withdrawal request is queued for the old token address, it can only be completed by transferring the old (now-deprecated) token. There is no path to redirect it to the new token address.

The oracle pricing loop iterates over `supportedAssetList` and calls `getAssetPrice` for every entry: [4](#0-3) 

If the price oracle for the old token address stops returning a valid price after migration, `_getTotalEthInProtocol()` reverts, breaking `updateRSETHPrice()` and halting the entire protocol's price-update mechanism.

---

### Impact Explanation

**Impact: Low → Temporary/Permanent Freezing of Funds**

- Users who deposited the migrated LST receive the deprecated old token on withdrawal — the protocol fails to deliver the promised (new) token.
- If the old token's price oracle becomes stale or reverts post-migration, `updateRSETHPrice()` is permanently broken, preventing new deposits and blocking the withdrawal unlock flow (`unlockQueue` depends on `lrtOracle.rsETHPrice()`). [5](#0-4) 

- The `removeSupportedAsset` guard ensures the old token cannot be cleanly removed while deposits exist, leaving the protocol in a permanently inconsistent state. [6](#0-5) 

---

### Likelihood Explanation

**Likelihood: Low**

LST token migrations are rare but precedented (e.g., stETH v1→v2 discussions, ETHx contract upgrades). The protocol explicitly supports stETH and ETHx as primary assets: [7](#0-6) 

Any migration by either token issuer would trigger this condition with no on-chain recovery path.

---

### Recommendation

- **Short-term**: Document this limitation explicitly. Clarify that if a supported LST migrates, the protocol must be paused and an off-chain coordination process (e.g., contacting the LST issuer for a migration window) must be followed.
- **Long-term**: Introduce an admin-controlled `migrateAsset(oldToken, newToken)` function that atomically remaps all protocol state (strategy, deposit limit, oracle, supported flag) from the old address to the new address, and provides a mechanism to convert held old tokens to new tokens before re-enabling withdrawals. Alternatively, lower `maxNegligibleAmount` to allow emergency removal even with residual deposits, paired with a separate emergency-sweep path.

---

### Proof of Concept

1. Protocol is live with stETH (`0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84`) as a supported asset with 50,000 stETH deposited across DepositPool, NDCs, and EigenLayer strategies.
2. Lido deploys stETH v2 at a new address and deprecates the old contract.
3. Admin attempts `removeSupportedAsset(oldStETH, index)` → reverts with `CannotRemoveAssetWithDeposits` because `getTotalAssetDeposits(oldStETH) >> maxNegligibleAmount`. [1](#0-0) 

4. Admin adds new stETH v2 address via `addNewSupportedAsset` — but the protocol still holds only old stETH; no conversion mechanism exists.
5. Users call `initiateWithdrawal(oldStETH, rsETHAmount)` — succeeds, but on `completeWithdrawal` they receive the deprecated old stETH token.
6. If the old stETH oracle stops returning a valid price, `_getTotalEthInProtocol()` reverts on every call, `updateRSETHPrice()` is permanently broken, and `unlockQueue` cannot execute — all pending withdrawals are frozen. [8](#0-7)

### Citations

**File:** contracts/LRTConfig.sol (L22-26)
```text
    mapping(address token => bool isSupported) public isSupportedAsset;
    mapping(address token => uint256 amount) public depositLimitByAsset;
    mapping(address token => address strategy) public override assetStrategy;

    address[] public supportedAssetList;
```

**File:** contracts/LRTConfig.sol (L54-57)
```text
        _setToken(LRTConstants.ST_ETH_TOKEN, stETH);
        _setToken(LRTConstants.ETHX_TOKEN, ethX);
        _addNewSupportedAsset(stETH, 100_000 ether);
        _addNewSupportedAsset(ethX, 100_000 ether);
```

**File:** contracts/LRTConfig.sol (L66-94)
```text
    function removeSupportedAsset(
        address asset,
        uint256 tokenIndex
    )
        external
        onlySupportedAsset(asset)
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(asset);

        if (supportedAssetList[tokenIndex] != asset) {
            revert TokenNotFoundError();
        }

        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }

        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;

        supportedAssetList[tokenIndex] = supportedAssetList[supportedAssetList.length - 1];
        supportedAssetList.pop();

        emit RemovedSupportedAsset(asset);
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

**File:** contracts/LRTWithdrawalManager.sol (L283-307)
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

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
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
