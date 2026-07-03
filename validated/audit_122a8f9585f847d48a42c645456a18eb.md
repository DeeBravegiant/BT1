### Title
Hardcoded 1:1 ETH Peg in `OneETHPriceOracle` Allows Over-Minting of rsETH on LST Depeg - (File: contracts/oracles/OneETHPriceOracle.sol)

---

### Summary

`OneETHPriceOracle` unconditionally returns `1e18` (1 ETH) as the exchange rate for any asset it is assigned to. When this oracle is configured for a liquid staking token (LST) such as stETH — which can and has historically traded below 1 ETH — any depositor can deposit the depegged LST and receive rsETH priced as if the LST were worth a full ETH, directly extracting value from existing rsETH holders.

---

### Finding Description

`OneETHPriceOracle.getAssetPrice()` is a pure function that ignores its `asset` argument and always returns `1e18`: [1](#0-0) 

This oracle is a deployed production contract (README lists it at `0x4cB8d6DCd56d6b371210E70837753F2a835160c4` on ETH mainnet) and is assignable to any supported asset via `LRTOracle.updatePriceOracleFor`. [2](#0-1) 

stETH is a supported asset initialized at protocol genesis: [3](#0-2) 

When a depositor calls `depositAsset`, the rsETH mint amount is computed as:

```
rsethAmountToMint = (depositAmount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()
``` [4](#0-3) 

If `OneETHPriceOracle` is the oracle for stETH, `getAssetPrice(stETH)` returns `1e18` regardless of the actual stETH/ETH market rate. A depositor submitting stETH worth 0.94 ETH receives rsETH priced at 1.00 ETH — a 6% over-mint.

The same inflated price feeds into `_getTotalEthInProtocol()`, which sums `totalAssetAmt * assetER` for all supported assets: [5](#0-4) 

This overstates protocol TVL, inflates `rsETHPrice`, and causes subsequent `updateRSETHPrice()` calls to mint excess protocol fee rsETH to the treasury, compounding the dilution.

---

### Impact Explanation

**Critical — Direct theft of user funds (existing rsETH holders).**

When stETH depegs (e.g., to 0.94 ETH as occurred during the June 2022 Celsius/3AC crisis):

1. An attacker deposits N stETH (market value: 0.94N ETH).
2. They receive rsETH priced at N ETH (hardcoded 1:1).
3. They redeem rsETH for ETH via the withdrawal path, receiving N ETH.
4. Net extraction: 0.06N ETH per deposit, funded by existing rsETH holders whose backing is diluted.

At scale (e.g., 10,000 stETH deposited at a 6% depeg), the attacker extracts ~600 ETH from the protocol, leaving existing holders with rsETH backed by less ETH than its stated price implies — protocol insolvency.

---

### Likelihood Explanation

**Medium-High.** stETH has demonstrably depegged before (June 2022: ~0.94 ETH). The `OneETHPriceOracle` is already deployed in production. The attack requires only that the oracle be assigned to stETH (an admin action that may already be in effect) and that stETH trades below 1 ETH — a market condition that recurs during stress events. No privileged attacker access is needed; any depositor can execute the over-mint once the depeg occurs.

---

### Recommendation

Replace `OneETHPriceOracle` for any LST that can trade below 1 ETH with a live price feed (e.g., Chainlink stETH/ETH). If a 1:1 assumption is intentional for a specific asset (e.g., WETH), scope the oracle to that asset explicitly with an address check, and document the assumption. At minimum, add a staleness/deviation check so that if the real market rate diverges beyond a threshold, the oracle reverts rather than silently returning a stale peg.

---

### Proof of Concept

```
// Precondition: OneETHPriceOracle is set as the price oracle for stETH in LRTOracle.
// stETH market price: 0.94 ETH (depeg event).

// Step 1: Attacker acquires 1000 stETH at market price (940 ETH cost).
// Step 2: Attacker calls:
LRTDepositPool.depositAsset(stETH, 1000e18, 0, "");

// Step 3: getRsETHAmountToMint computes:
//   assetPrice = OneETHPriceOracle.getAssetPrice(stETH) = 1e18  (hardcoded)
//   rsethAmountToMint = (1000e18 * 1e18) / rsETHPrice
//   → attacker receives rsETH equivalent to 1000 ETH, not 940 ETH.

// Step 4: Attacker initiates withdrawal for rsETH → receives ~1000 ETH.
// Net profit: ~60 ETH extracted from existing rsETH holders.
// Existing holders' rsETH is now backed by 60 ETH less than its stated price implies.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/oracles/OneETHPriceOracle.sol (L8-12)
```text
contract OneETHPriceOracle is IPriceFetcher {
    /// @return assetPrice 1e18 as the exchange rate of asset/ETH
    function getAssetPrice(address) external pure returns (uint256) {
        return 1e18;
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

**File:** contracts/LRTOracle.sol (L331-343)
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
```

**File:** contracts/LRTConfig.sol (L54-57)
```text
        _setToken(LRTConstants.ST_ETH_TOKEN, stETH);
        _setToken(LRTConstants.ETHX_TOKEN, ethX);
        _addNewSupportedAsset(stETH, 100_000 ether);
        _addNewSupportedAsset(ethX, 100_000 ether);
```

**File:** contracts/LRTDepositPool.sol (L515-520)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
