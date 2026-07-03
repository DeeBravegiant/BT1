Audit Report

## Title
Stale `rsETHPrice` After Asset Oracle Address Change Allows Excess rsETH Minting - (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updatePriceOracleFor()` replaces an asset's price oracle without first syncing the cached `rsETHPrice` storage variable. After the swap, `getAssetPrice(asset)` immediately returns the new oracle's live price while `rsETHPrice` still reflects the old oracle's prices. Any depositor who transacts in the window between the oracle change and the next `updateRSETHPrice()` call receives a mis-priced rsETH amount, diluting all existing rsETH holders.

## Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate as a cached storage variable `rsETHPrice`: [1](#0-0) 

This value is only updated when `_updateRsETHPrice()` is explicitly called: [2](#0-1) 

The asset-level oracle address is changed by `updatePriceOracleFor()`: [3](#0-2) 

Neither `updatePriceOracleFor()` nor `updatePriceOracleForValidated()` calls `_updateRsETHPrice()` before or after the swap: [4](#0-3) 

After the swap, `getAssetPrice(asset)` reads live from the new oracle: [5](#0-4) 

But `LRTDepositPool.getRsETHAmountToMint()` divides by the stale cached value: [6](#0-5) 

**Exploit flow:**
1. stETH oracle returns 1.05 ETH/stETH; `rsETHPrice` is cached at `P_old` computed using that price.
2. Admin calls `updatePriceOracleFor(stETH, newOracle)` where `newOracle.getAssetPrice(stETH)` returns 1.10 ETH/stETH.
3. `rsETHPrice` remains `P_old`. The correct post-swap price `P_new > P_old` because total protocol ETH value increased.
4. Attacker calls `depositAsset(stETH, amount, 0, "")` before `updateRSETHPrice()` is called.
5. `getRsETHAmountToMint` computes `(amount × 1.10e18) / P_old` instead of the correct `(amount × 1.10e18) / P_new`.
6. Attacker receives excess rsETH, diluting all existing holders.

The `minRSETHAmountExpected` slippage guard in `_beforeDeposit()` only protects the depositor from receiving too little; it provides no protection to existing holders against over-minting: [7](#0-6) 

## Impact Explanation

Existing rsETH holders suffer dilution: the excess rsETH minted to the attacker represents a proportional reduction in the ETH backing per rsETH share. This constitutes theft of unclaimed yield from all current holders. The magnitude scales with (a) the price delta between old and new oracle and (b) the weight of the affected asset in total protocol TVL. For a dominant asset like stETH, the impact is material.

**Impact classification: High — Theft of unclaimed yield.**

## Likelihood Explanation

Oracle address changes are routine admin operations (e.g., migrating Chainlink feeds or switching oracle adapters). The window between `updatePriceOracleFor()` and the next `updateRSETHPrice()` call is publicly observable on-chain. A MEV bot watching the mempool can sandwich the oracle-change transaction with a large deposit, exploiting the stale price in the same block. No special permissions are required for the deposit itself — `depositAsset()` is a public function callable by any user. [8](#0-7) 

## Recommendation

Call `_updateRsETHPrice()` inside `updatePriceOracleFor()` before replacing the oracle address:

```solidity
function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
    if (lrtConfig.isSupportedAsset(asset)) {
        UtilLib.checkNonZeroAddress(priceOracle);
    }
    _updateRsETHPrice();          // sync cached price before oracle swap
    assetPriceOracle[asset] = priceOracle;
    emit AssetPriceOracleUpdate(asset, priceOracle);
}
```

Apply the same fix to `updatePriceOracleForValidated()`. This ensures `rsETHPrice` reflects the old oracle's final price before the new oracle's prices take effect, eliminating the desync window.

## Proof of Concept

Minimal Foundry fork test sequence:

1. Deploy protocol with stETH as the only supported asset. Set `mockOracleA.getAssetPrice(stETH) = 1.05e18`. Call `updateRSETHPrice()` to set `rsETHPrice = 1.05e18`.
2. Seed the protocol with 100e18 stETH from an existing depositor so `rsETHPrice` is non-trivially cached.
3. Deploy `mockOracleB` where `getAssetPrice(stETH) = 1.10e18`.
4. Admin calls `updatePriceOracleFor(stETH, address(mockOracleB))`. Assert `rsETHPrice` is still `1.05e18`.
5. Attacker calls `depositAsset(stETH, 1e18, 0, "")`. Record `rsethMinted`.
6. Call `updateRSETHPrice()`. Record `rsETHPrice` as `P_new`.
7. Assert `rsethMinted > 1e18 * 1.10e18 / P_new` — attacker received excess rsETH.
8. Assert existing holder's redemption value decreased, confirming dilution. [3](#0-2) [9](#0-8)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L101-108)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
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

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L666-669)
```text

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
