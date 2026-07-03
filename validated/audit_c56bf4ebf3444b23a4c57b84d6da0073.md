### Title
`LRTOracle#_getTotalEthInProtocol` reverts for any supported asset without a price oracle, causing DOS on `updateRSETHPrice` - (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` iterates over every asset in `lrtConfig.getSupportedAssetList()` and calls `getAssetPrice(asset)` for each. `getAssetPrice` enforces the `onlySupportedOracle` modifier, which reverts with `AssetOracleNotSupported` if `assetPriceOracle[asset] == address(0)`. Because `LRTConfig.addNewSupportedAsset()` adds an asset to the supported list with no requirement that a corresponding oracle be registered in `LRTOracle`, any supported asset that lacks an oracle entry permanently breaks `updateRSETHPrice()` until the oracle is set.

---

### Finding Description

`LRTConfig.addNewSupportedAsset()` registers a new asset in `supportedAssetList` and `isSupportedAsset` with no coupling to `LRTOracle.assetPriceOracle`: [1](#0-0) 

The oracle mapping is managed entirely separately via `LRTOracle.updatePriceOracleFor()` or `updatePriceOracleForValidated()`, both of which are independent admin calls: [2](#0-1) 

`_getTotalEthInProtocol()` fetches the full supported asset list and calls `getAssetPrice` on every entry: [3](#0-2) 

`getAssetPrice` carries the `onlySupportedOracle` modifier that hard-reverts when the oracle mapping is zero: [4](#0-3) [5](#0-4) 

`_getTotalEthInProtocol()` is called unconditionally inside `_updateRsETHPrice()`: [6](#0-5) 

which is the sole body of the public `updateRSETHPrice()`: [7](#0-6) 

The result is that a single asset in `supportedAssetList` with no oracle entry causes every call to `updateRSETHPrice()` to revert.

---

### Impact Explanation

`updateRSETHPrice()` is responsible for:

1. **Updating the stored `rsETHPrice`** — the exchange rate used by `LRTDepositPool.getRsETHAmountToMint()` and `LRTWithdrawalManager.getExpectedAssetAmount()` to compute mint and redemption amounts. A stale price means users receive incorrect rsETH amounts on deposit and incorrect asset amounts on withdrawal.
2. **Minting protocol fee rsETH** — the fee accrual mechanism inside `_updateRsETHPrice()` cannot execute, permanently freezing unclaimed protocol yield for the duration of the DOS.
3. **Triggering the price-drop auto-pause** — the downside protection that pauses `LRTDepositPool` and `LRTWithdrawalManager` when the price falls too far cannot fire, leaving the protocol exposed to bad-rate deposits/withdrawals during a market downturn.

Impact classification: **Medium — Temporary freezing of unclaimed yield** (protocol fees cannot be minted) and **Low — Contract fails to deliver promised returns** (stale rsETH price used for all mint/redeem calculations). [8](#0-7) 

---

### Likelihood Explanation

Adding a new supported asset (`TIME_LOCK_ROLE`) and registering its oracle (`LRT_ADMIN`) are two separate privileged transactions with no on-chain enforcement of ordering or co-occurrence. In any multi-step asset onboarding workflow there is a window — potentially extended by governance delays or human error — during which the asset exists in `supportedAssetList` but `assetPriceOracle[asset]` is still zero. This is a realistic operational scenario, not a compromise. [9](#0-8) 

---

### Recommendation

Add a zero-oracle guard inside `_getTotalEthInProtocol()` so that assets without a registered oracle are skipped (contributing zero value) rather than reverting the entire call:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    if (assetPriceOracle[asset] == address(0)) {
        unchecked { ++assetIdx; }
        continue;
    }
    uint256 assetER = getAssetPrice(asset);
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    unchecked { ++assetIdx; }
}
```

Alternatively, enforce atomicity at the `LRTConfig` level by requiring that a valid oracle address be supplied when calling `addNewSupportedAsset`, or add an on-chain check that the oracle is registered before the asset is added to the list.

---

### Proof of Concept

1. Admin (TIME_LOCK_ROLE) calls `LRTConfig.addNewSupportedAsset(newAsset, depositLimit)`.
   - `newAsset` is pushed into `supportedAssetList`; `isSupportedAsset[newAsset] = true`.
   - `LRTOracle.assetPriceOracle[newAsset]` remains `address(0)`.

2. Any caller (including a keeper bot or any user) calls `LRTOracle.updateRSETHPrice()`.

3. Execution path:
   ```
   updateRSETHPrice()
     → _updateRsETHPrice()
       → _getTotalEthInProtocol()
         → lrtConfig.getSupportedAssetList()   // returns [..., newAsset]
         → getAssetPrice(newAsset)
           → onlySupportedOracle(newAsset)      // assetPriceOracle[newAsset] == 0
             → revert AssetOracleNotSupported()
   ```

4. `updateRSETHPrice()` reverts for every caller until the admin separately calls `updatePriceOracleFor(newAsset, oracle)`.

5. During this window: `rsETHPrice` is stale, protocol fees cannot be minted, and the price-drop auto-pause cannot trigger. [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/LRTConfig.sol (L99-101)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
    }
```

**File:** contracts/LRTConfig.sol (L106-118)
```text
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L113-118)
```text
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L214-231)
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
