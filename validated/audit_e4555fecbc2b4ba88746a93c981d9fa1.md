### Title
Missing price oracle for newly added supported asset causes `updateRSETHPrice()` to revert, permanently freezing unclaimed protocol fee yield - (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` iterates over every asset in `LRTConfig.supportedAssetList` and calls `getAssetPrice(asset)` for each. `getAssetPrice` reverts via the `onlySupportedOracle` modifier if `assetPriceOracle[asset] == address(0)`. Because `addNewSupportedAsset()` in `LRTConfig` and `updatePriceOracleFor()` in `LRTOracle` are independent, ungated transactions with no enforced ordering, any asset added to the supported list without a corresponding oracle entry causes every subsequent call to `updateRSETHPrice()` to revert. Protocol fee rsETH that should have been minted during the blocked period is permanently lost.

---

### Finding Description

**Root cause — `LRTOracle.sol`**

`getAssetPrice` is guarded by `onlySupportedOracle`:

```solidity
// LRTOracle.sol L40-44
modifier onlySupportedOracle(address asset) {
    if (assetPriceOracle[asset] == address(0)) {
        revert AssetOracleNotSupported();
    }
    _;
}
``` [1](#0-0) 

`_getTotalEthInProtocol()` calls `getAssetPrice` for **every** asset in the supported list:

```solidity
// LRTOracle.sol L336-339
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // reverts if oracle not set
    ...
}
``` [2](#0-1) 

`_getTotalEthInProtocol()` is called unconditionally inside `_updateRsETHPrice()`, which is the body of both the public `updateRSETHPrice()` and the manager-only `updateRSETHPriceAsManager()`. [3](#0-2) 

**Configuration gap — `LRTConfig.sol`**

`addNewSupportedAsset()` requires only `TIME_LOCK_ROLE` and pushes the asset into `supportedAssetList` with no requirement that a price oracle already exists in `LRTOracle`:

```solidity
// LRTConfig.sol L99-101
function addNewSupportedAsset(address asset, uint256 depositLimit)
    external onlyRole(LRTConstants.TIME_LOCK_ROLE)
{
    _addNewSupportedAsset(asset, depositLimit);
}
``` [4](#0-3) 

`updatePriceOracleFor()` in `LRTOracle` is a separate call requiring `onlyLRTAdmin`. There is no atomic path that adds an asset and sets its oracle in one transaction, and no on-chain enforcement that the oracle must be set before or at the same time as the asset is added. [5](#0-4) 

---

### Impact Explanation

**Permanent freezing of unclaimed yield (Medium)**

Protocol fee rsETH is minted exclusively inside `_updateRsETHPrice()`:

```solidity
// LRTOracle.sol L299-308
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
    if (rsethAmountToMintAsProtocolFee > 0) {
        IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
    }
}
``` [6](#0-5) 

While `updateRSETHPrice()` is blocked, every block of staking rewards that accrues goes unaccounted and the corresponding fee rsETH is never minted. Those fees cannot be retroactively recovered once the oracle is eventually set, because the price update that would have captured them never ran. The rsETH price also becomes stale, disabling the automatic price-drop pause protection built into `_updateRsETHPrice()`. [7](#0-6) 

---

### Likelihood Explanation

`addNewSupportedAsset()` (timelock role) and `updatePriceOracleFor()` (admin role) are held by different actors and executed in separate transactions with no on-chain dependency. A new LST can be legitimately added to the protocol — as has happened historically with `stETH`, `ethX`, and `sfrxETH` — and the oracle registration step can be delayed or omitted entirely. The window between asset addition and oracle registration is sufficient to permanently lose fee yield for that period. This is a realistic operational oversight, directly analogous to the reference report's "chainId not set for collateral" scenario.

---

### Recommendation

1. In `addNewSupportedAsset()` (or its internal helper `_addNewSupportedAsset()`), add a cross-contract check that `LRTOracle.assetPriceOracle[asset] != address(0)` before pushing the asset into `supportedAssetList`.
2. Alternatively, require that `updatePriceOracleFor()` is called atomically with `addNewSupportedAsset()` via a single timelock proposal that batches both calls.
3. At minimum, document the mandatory sequencing and add an off-chain monitoring alert that fires whenever `supportedAssetList` contains an asset with no corresponding oracle entry.

---

### Proof of Concept

1. Protocol is live with `stETH` and `ethX` both having oracles set. `updateRSETHPrice()` succeeds normally.
2. Timelock executes `LRTConfig.addNewSupportedAsset(newLST, depositLimit)`. `newLST` is now in `supportedAssetList`. No oracle is set yet in `LRTOracle` for `newLST` (`assetPriceOracle[newLST] == address(0)`).
3. Any caller (including the public) calls `LRTOracle.updateRSETHPrice()`.
4. Execution reaches `_getTotalEthInProtocol()` → loop reaches `newLST` → `getAssetPrice(newLST)` → `onlySupportedOracle` modifier fires → `revert AssetOracleNotSupported()`.
5. `updateRSETHPrice()` and `updateRSETHPriceAsManager()` both revert. The rsETH price is frozen at its last value.
6. All staking rewards that accrue during this window are never captured in the price, and the corresponding protocol fee rsETH is permanently lost to the treasury.
7. Admin eventually calls `updatePriceOracleFor(newLST, oracle)`. `updateRSETHPrice()` resumes, but the missed fee yield from steps 3–6 is irrecoverable.

### Citations

**File:** contracts/LRTOracle.sol (L40-44)
```text
    modifier onlySupportedOracle(address asset) {
        if (assetPriceOracle[asset] == address(0)) {
            revert AssetOracleNotSupported();
        }
        _;
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

**File:** contracts/LRTOracle.sol (L299-308)
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
```

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTConfig.sol (L99-101)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
    }
```
