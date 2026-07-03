Audit Report

## Title
`OneETHPriceOracle` Hardcodes Asset Price to 1 ETH, Enabling Arbitrage and Protocol Insolvency if Assigned LST Depegs - (File: `contracts/oracles/OneETHPriceOracle.sol`)

## Summary
`OneETHPriceOracle` unconditionally returns `1e18` for any asset's price regardless of market conditions. If this oracle is assigned to an LST that subsequently trades below 1 ETH, any unprivileged depositor can buy the depegged LST cheaply on the open market and deposit it into `LRTDepositPool` at the hardcoded 1 ETH valuation, receiving more rsETH than the deposited collateral is worth. This directly dilutes existing rsETH holders and, in a sustained depeg, causes protocol insolvency.

## Finding Description
`OneETHPriceOracle.getAssetPrice()` always returns `1e18` with no market-price check: [1](#0-0) 

This oracle is registered for supported assets via `LRTOracle.updatePriceOracleFor()` (admin-gated, but intended production use): [2](#0-1) 

The registered price is consumed directly in `LRTDepositPool.getRsETHAmountToMint()`: [3](#0-2) 

`lrtOracle.rsETHPrice()` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called: [4](#0-3) 

Critically, `_getTotalEthInProtocol()` — which computes the rsETH price — also calls `getAssetPrice(asset)` for each supported asset: [5](#0-4) 

Because `OneETHPriceOracle` always returns `1e18`, even after a depeg, `_getTotalEthInProtocol()` continues to overstate the protocol's ETH backing. Calling `updateRSETHPrice()` after the depeg does not correct the rsETH price — it remains inflated. The `pricePercentageLimit` guard only triggers when the computed rsETH price changes significantly; since the oracle never reflects the depeg, the rsETH price does not change and the guard never fires: [6](#0-5) 

Similarly, the downside-protection pause (lines 270–281) only triggers if the computed rsETH price drops below `highestRsethPrice` by the threshold — which it never does when the oracle is hardcoded: [7](#0-6) 

**Exploit path:**
1. stETH is a supported asset; `assetPriceOracle[stETH] = OneETHPriceOracle`.
2. stETH depegs to 0.90 ETH on the open market.
3. Attacker buys 1,000 stETH for 900 ETH.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, minRSETH, "")`.
5. `getAssetPrice(stETH)` returns `1e18`; `rsethAmountToMint = (1000e18 × 1e18) / rsETHPrice` — attacker receives rsETH backed by 1,000 ETH of protocol value.
6. Attacker paid 900 ETH, received ~1,000 ETH worth of rsETH. ~100 ETH extracted from existing rsETH holders per iteration.

No special permissions are required; `depositAsset()` is a public function: [8](#0-7) 

## Impact Explanation
**Critical — Direct theft of user funds / Protocol insolvency.**

Every deposit of a depegged LST at the hardcoded 1 ETH price mints rsETH backed by less real ETH than claimed, directly diluting the ETH backing of all existing rsETH holders. In a sustained depeg, the protocol's TVL is permanently overstated and redemptions cannot be fully honored, constituting protocol insolvency. Both match allowed Critical impacts.

## Likelihood Explanation
**Medium.** LST depeg events have historical precedent (stETH traded at ~0.94 ETH during the Merge period). `OneETHPriceOracle` is a deployed production contract explicitly designed for "hard-pegged" assets; any LST assigned this oracle that subsequently depegs — even temporarily — opens the arbitrage window. No special permissions are required; any depositor can trigger it via the public `depositAsset()` function. The exploit is repeatable for as long as the depeg persists and the oracle assignment is unchanged.

## Recommendation
Do not assign `OneETHPriceOracle` to any LST that can trade below 1 ETH. For every supported LST, use a live price feed (Chainlink or the LST's own on-chain exchange rate contract) so that a depeg is immediately reflected in deposit calculations. If a 1:1 peg assumption is required for a specific asset, add a staleness/deviation circuit-breaker that pauses deposits when the live price deviates beyond a configurable threshold from `1e18`, rather than silently accepting the hardcoded value.

## Proof of Concept
```
Setup:
  - stETH is a supported asset in LRTDepositPool
  - LRTOracle.assetPriceOracle[stETH] = OneETHPriceOracle
  - rsETHPrice (stored) = 1.05e18 (last updated when stETH = 1 ETH)

Market event:
  - stETH depegs: market price = 0.90 ETH

Attacker steps:
  1. Buy 1000 stETH on Curve/Uniswap for 900 ETH
  2. Call LRTDepositPool.depositAsset(stETH, 1000e18, minRSETH, "")
  3. getRsETHAmountToMint:
       assetPrice = OneETHPriceOracle.getAssetPrice(stETH) = 1e18
       rsethAmountToMint = (1000e18 * 1e18) / 1.05e18 ≈ 952.38 rsETH
  4. 952.38 rsETH × 1.05 ETH/rsETH = 1000 ETH of rsETH value received
  5. Attacker paid 900 ETH, received 1000 ETH worth of rsETH
  6. Profit: ~100 ETH extracted from existing rsETH holders per round

Foundry fork test plan:
  - Fork mainnet; deploy/configure LRTDepositPool + LRTOracle with OneETHPriceOracle for stETH
  - Simulate stETH depeg by mocking getAssetPrice to return 0.9e18 from a live feed
    (or use vm.mockCall on the Chainlink feed)
  - Record attacker ETH balance before/after depositAsset
  - Assert rsETH minted > ETH deposited (in ETH terms), confirming over-issuance
  - Assert existing rsETH holders' pro-rata ETH backing decreased
```

### Citations

**File:** contracts/oracles/OneETHPriceOracle.sol (L10-12)
```text
    function getAssetPrice(address) external pure returns (uint256) {
        return 1e18;
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
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

**File:** contracts/LRTOracle.sol (L270-281)
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
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

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
