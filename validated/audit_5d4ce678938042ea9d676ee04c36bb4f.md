### Title
Missing Oracle Enforcement on Asset Addition Causes `updateRSETHPrice` to Revert Permanently — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTConfig.addNewSupportedAsset` adds an asset to `supportedAssetList` without requiring a corresponding price oracle to be registered in `LRTOracle`. If `updateRSETHPrice` (or `updateRSETHPriceAsManager`) is called while any supported asset has `assetPriceOracle[asset] == address(0)`, the call reverts unconditionally, permanently blocking all protocol fee minting until an admin manually sets the oracle.

---

### Finding Description

**Root cause — `addNewSupportedAsset` has no oracle requirement:**

`LRTConfig.addNewSupportedAsset` pushes the asset into `supportedAssetList` and sets `isSupportedAsset[asset] = true`, but makes no call to `LRTOracle.updatePriceOracleFor`. [1](#0-0) 

**`updatePriceOracleFor` actively prevents fixing the gap for a supported asset with zero address:**

The guard `UtilLib.checkNonZeroAddress(priceOracle)` is only applied when the asset is already supported, meaning you cannot accidentally clear an oracle for a live asset. However, the inverse gap — a newly supported asset with no oracle — is not prevented. [2](#0-1) 

**`_getTotalEthInProtocol` iterates every supported asset and calls `getAssetPrice`:** [3](#0-2) 

**`getAssetPrice` reverts via `onlySupportedOracle` if the oracle is `address(0)`:** [4](#0-3) [5](#0-4) 

**Both public entry points call `_updateRsETHPrice` → `_getTotalEthInProtocol`:** [6](#0-5) 

The revert propagates all the way up, making it impossible to update the rsETH price or mint protocol fees.

**Second reachable path — oracle cleared while asset is unsupported, then re-added:**

`updatePriceOracleFor` allows `priceOracle = address(0)` when `isSupportedAsset[asset]` is `false`. If an asset is removed, its oracle is zeroed, and the asset is later re-added via `addNewSupportedAsset`, the same broken state is reached. [2](#0-1) 

---

### Impact Explanation

All protocol fee minting flows through `_updateRsETHPrice`. [7](#0-6) 

While the oracle gap persists, every call to `updateRSETHPrice` or `updateRSETHPriceAsManager` reverts. Yield accrues in the protocol TVL but no rsETH fee shares are minted to the treasury. If the oracle is never set (e.g., the asset onboarding is incomplete or the admin forgets), the freeze is permanent. Even if eventually fixed, the daily fee cap (`maxFeeMintAmountPerDay`) may prevent full recovery of the accumulated unclaimed yield in a single transaction. [8](#0-7) 

**Impact: Medium — Permanent freezing of unclaimed yield.**

---

### Likelihood Explanation

The scenario requires no attacker. It arises from a normal, privileged admin operation: adding a new supported asset via `addNewSupportedAsset` (gated by `TIME_LOCK_ROLE`) without atomically setting its oracle. The two operations are in separate contracts with no enforced coupling. Any deployment that onboards a new asset in a multi-step process (e.g., governance vote to add asset, separate transaction to configure oracle) is exposed during the gap. This is a realistic operational sequence.

---

### Recommendation

1. **Enforce oracle presence at asset addition time.** Modify `LRTConfig.addNewSupportedAsset` (or add a wrapper) to require that a valid oracle address is provided and registered in `LRTOracle` atomically before the asset enters `supportedAssetList`.

2. **Alternatively, make `_getTotalEthInProtocol` skip assets with no oracle** (emit a warning event) rather than reverting, so a misconfigured asset degrades gracefully instead of bricking the entire price update.

3. **Prevent `updatePriceOracleFor` from accepting `address(0)` unconditionally**, not just when the asset is currently supported.

---

### Proof of Concept

```solidity
// Foundry test (local, no mainnet)
function test_missingOracleBlocksFees() public {
    // 1. Admin adds a new supported asset (no oracle set)
    vm.prank(timeLockAdmin);
    lrtConfig.addNewSupportedAsset(address(newToken), 100_000 ether);

    // assetPriceOracle[newToken] == address(0) at this point

    // 2. Anyone calls updateRSETHPrice — reverts with AssetOracleNotSupported
    vm.expectRevert(ILRTOracle.AssetOracleNotSupported.selector);
    lrtOracle.updateRSETHPrice();

    // 3. Even the manager path reverts
    vm.prank(manager);
    vm.expectRevert(ILRTOracle.AssetOracleNotSupported.selector);
    lrtOracle.updateRSETHPriceAsManager();

    // 4. No fee rsETH is minted; yield accumulates unclaimed
}
```

### Citations

**File:** contracts/LRTConfig.sol (L99-118)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
    }

    /// @dev private function to add a new supported asset
    /// @param asset Asset address
    /// @param depositLimit Deposit limit for the asset
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
    }
```

**File:** contracts/LRTOracle.sol (L40-45)
```text
    modifier onlySupportedOracle(address asset) {
        if (assetPriceOracle[asset] == address(0)) {
            revert AssetOracleNotSupported();
        }
        _;
    }
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L113-119)
```text
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
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
