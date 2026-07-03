### Title
`instantWithdrawal` Burns rsETH Without Updating `rsETHPrice` Checkpoint, Enabling Excess Protocol Fee Minting — (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal` burns rsETH tokens from the caller without triggering a recalculation of `rsETHPrice` or `highestRsethPrice` in `LRTOracle`. This is the direct analog of the reported bug: a withdrawal path that changes the share supply without updating the price checkpoint. The stale `rsETHPrice` is subsequently used as the fee-calculation baseline in `_updateRsETHPrice`, causing the protocol to perceive phantom rewards and mint excess protocol fees, diluting rsETH holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice` computes the fee baseline as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);   // LRTOracle.sol:234
```

`rsETHPrice` here is the **stored** (last-updated) price, not a live computation. The fee is minted only when `totalETHInProtocol > previousTVL`.

`LRTWithdrawalManager.instantWithdrawal` burns rsETH directly:

```solidity
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);  // LRTWithdrawalManager.sol:229
```

and redeems assets from the unstaking vault:

```solidity
unstakingVault.redeem(asset, assetAmountUnlocked);   // LRTWithdrawalManager.sol:235
```

Neither step calls `updateRSETHPrice()`. After the burn:

- `rsethSupply` decreases by `rsETHUnstaked`.
- `rsETHPrice` (stored) remains at the pre-burn value `P`.
- `totalETHInProtocol` as seen by `_getTotalEthInProtocol()` may remain unchanged if the unstaking vault balance is not included in `ILRTDepositPool.getTotalAssetDeposits` (assets were already moved out of EigenLayer/NodeDelegators into the vault before this call).

When `updateRSETHPrice()` is next called:

```
previousTVL = (S - X) * P          // understated by X*P
totalETHInProtocol = T              // unchanged (unstaking vault not in TVL)
rewardAmount = T - (S-X)*P = T - S*P + X*P
```

If `T ≈ S*P` (price was accurate at last update), `rewardAmount ≈ X*P` — a phantom reward equal to the full value of the burned rsETH. The protocol then mints:

```solidity
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;  // LRTOracle.sol:246
uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);  // LRTOracle.sol:301
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);   // LRTOracle.sol:306
```

This excess rsETH is minted to the treasury, diluting all remaining rsETH holders. The same issue applies to `unlockQueue`, which also burns rsETH without updating the oracle.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every `instantWithdrawal` call that precedes an `updateRSETHPrice` call causes the protocol to mint excess fee rsETH proportional to `rsETHUnstaked * rsETHPrice * protocolFeeInBPS / 10_000`. This is extracted from the collective value backing all rsETH holders and transferred to the treasury. The effect compounds across multiple withdrawals between oracle updates.

---

### Likelihood Explanation

**Medium.** `instantWithdrawal` is a permissionless user-callable function (gated only by `isInstantWithdrawalEnabled[asset]`). `updateRSETHPrice()` is called periodically off-chain, not atomically with withdrawals. Any withdrawal that occurs between two oracle updates creates the phantom-reward window. With active withdrawal usage, this occurs continuously.

---

### Recommendation

Call `updateRSETHPrice()` (or an internal `_updateRsETHPrice()`) at the **beginning** of `instantWithdrawal` and `unlockQueue`, before any rsETH is burned, to ensure `rsETHPrice` and `highestRsethPrice` reflect the current state prior to the supply change. This mirrors the correct pattern: the checkpoint must be refreshed before the share supply is altered.

---

### Proof of Concept

1. Protocol state: `rsethSupply = 1000e18`, `rsETHPrice = 1.05e18`, `totalETHInProtocol = 1050e18`, `protocolFeeInBPS = 1000` (10%).
2. User calls `instantWithdrawal(asset, 100e18, "")`:
   - Burns 100e18 rsETH → `rsethSupply = 900e18`.
   - Redeems ~105e18 worth of assets from unstaking vault (not counted in TVL).
   - `rsETHPrice` in oracle remains `1.05e18`.
3. Anyone calls `updateRSETHPrice()`:
   - `previousTVL = 900e18 * 1.05e18 / 1e18 = 945e18`.
   - `totalETHInProtocol = 1050e18` (unstaking vault not counted, assets already left EigenLayer).
   - `rewardAmount = 1050e18 - 945e18 = 105e18` (phantom).
   - `protocolFeeInETH = 105e18 * 1000 / 10000 = 10.5e18`.
   - `newRsETHPrice ≈ (1050e18 - 10.5e18) / 900e18 ≈ 1.155e18`.
   - `rsethAmountToMintAsProtocolFee = 10.5e18 / 1.155e18 ≈ 9.09e18` rsETH minted to treasury.
4. Without the stale-price bug, no phantom reward would exist (the burn and asset redemption are proportional), and zero or minimal fees would be minted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L268-319)
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
```

**File:** contracts/LRTOracle.sol (L228-250)
```text
        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
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

**File:** contracts/LRTOracle.sol (L299-311)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }
```
