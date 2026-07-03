### Title
Missing oracle registration enforcement when adding a new supported asset causes `updateRSETHPrice()` to permanently revert — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTConfig.addNewSupportedAsset()` and `LRTOracle.updatePriceOracleFor()` are two independent, unlinked operations. When a new asset is added to `LRTConfig.supportedAssetList` without a corresponding price oracle being registered in `LRTOracle`, the public `updateRSETHPrice()` function reverts for every caller. This freezes protocol fee accrual (unclaimed yield), stales the rsETH exchange rate used by deposits and withdrawals, and disables the automatic price-drop protection that pauses the protocol.

---

### Finding Description

`LRTOracle._getTotalEthInProtocol()` iterates over every asset returned by `lrtConfig.getSupportedAssetList()` and calls `getAssetPrice(asset)` for each one:

```solidity
// LRTOracle.sol L331-L349
function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
    address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
    address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
    uint256 supportedAssetCount = supportedAssets.length;

    for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
        address asset = supportedAssets[assetIdx];
        uint256 assetER = getAssetPrice(asset);   // ← reverts if oracle not set
        ...
    }
}
```

`getAssetPrice` is guarded by `onlySupportedOracle`:

```solidity
// LRTOracle.sol L40-L45
modifier onlySupportedOracle(address asset) {
    if (assetPriceOracle[asset] == address(0)) {
        revert AssetOracleNotSupported();
    }
    _;
}
```

`LRTConfig.addNewSupportedAsset()` adds an asset to `supportedAssetList` with no requirement that a price oracle already exists in `LRTOracle`:

```solidity
// LRTConfig.sol L99-L118
function addNewSupportedAsset(address asset, uint256 depositLimit)
    external onlyRole(LRTConstants.TIME_LOCK_ROLE)
{
    _addNewSupportedAsset(asset, depositLimit);
}

function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
    ...
    isSupportedAsset[asset] = true;
    supportedAssetList.push(asset);
    depositLimitByAsset[asset] = depositLimit;
    emit AddedNewSupportedAsset(asset, depositLimit);
}
```

There is no cross-contract check: `LRTOracle.updatePriceOracleFor()` is a completely separate call that must be made independently. If the oracle registration is omitted or delayed, `_getTotalEthInProtocol()` reverts on the new asset, causing `updateRSETHPrice()` (public) and `updateRSETHPriceAsManager()` (manager-only) to both revert.

---

### Impact Explanation

**Medium — Temporary freezing of unclaimed yield.**

While the oracle mismatch persists:

1. **Protocol fee minting is frozen.** `_updateRsETHPrice()` mints rsETH fees to the treasury on every successful call. With the function reverting, no fees accrue. [1](#0-0) 

2. **rsETH price becomes stale.** `rsETHPrice` (a storage variable) is only updated inside `_updateRsETHPrice()`. Deposits via `LRTDepositPool.getRsETHAmountToMint()` and withdrawals via `LRTWithdrawalManager.getExpectedAssetAmount()` both read this stale value, causing users to receive incorrect rsETH or asset amounts. [2](#0-1) 

3. **Automatic price-drop protection is disabled.** The logic that pauses `LRTDepositPool` and `LRTWithdrawalManager` when the rsETH price falls beyond the threshold cannot execute, leaving the protocol unprotected against slashing events during the window. [3](#0-2) 

---

### Likelihood Explanation

`addNewSupportedAsset` (on `LRTConfig`) and `updatePriceOracleFor` (on `LRTOracle`) are two separate privileged transactions with no on-chain coupling. A timelock admin adding a new LST to the protocol in one transaction and deferring the oracle registration — even briefly — is a realistic operational mistake. The protocol already supports multiple assets (stETH, ETHx, ETH) and is designed to expand, making this scenario plausible on every future asset addition. [4](#0-3) 

---

### Recommendation

Enforce consistency between the two registries at the point of asset addition. One approach: require that a valid price oracle address is passed alongside the new asset in `addNewSupportedAsset`, and atomically register it in `LRTOracle` within the same transaction (or via a factory contract that calls both). Alternatively, add a guard inside `_getTotalEthInProtocol()` that skips assets with no oracle rather than reverting, and emit a warning event. [5](#0-4) 

---

### Proof of Concept

1. Admin calls `LRTConfig.addNewSupportedAsset(newLST, depositLimit)`. `newLST` is appended to `supportedAssetList`. No oracle is set in `LRTOracle` yet. [6](#0-5) 

2. Any caller (including a keeper bot or any EOA) calls `LRTOracle.updateRSETHPrice()`. [7](#0-6) 

3. Execution reaches `_getTotalEthInProtocol()`, which iterates `supportedAssets`. When `assetIdx` reaches `newLST`, it calls `getAssetPrice(newLST)`. [8](#0-7) 

4. `onlySupportedOracle` checks `assetPriceOracle[newLST] == address(0)` → `true` → reverts with `AssetOracleNotSupported`. [9](#0-8) 

5. `updateRSETHPrice()` reverts. Every subsequent call reverts identically. `rsETHPrice` is frozen at its last value. Protocol fee minting and price-drop protection are both inoperative until `LRTOracle.updatePriceOracleFor(newLST, oracle)` is called. [10](#0-9)

### Citations

**File:** contracts/LRTOracle.sol (L40-45)
```text
    modifier onlySupportedOracle(address asset) {
        if (assetPriceOracle[asset] == address(0)) {
            revert AssetOracleNotSupported();
        }
        _;
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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

**File:** contracts/LRTOracle.sol (L313-315)
```text
        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
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
