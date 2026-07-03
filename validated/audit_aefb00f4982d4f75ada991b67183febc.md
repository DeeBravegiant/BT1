### Title
`OneETHPriceOracle` Hardcodes Asset Price to 1 ETH, Enabling Arbitrage and Protocol Insolvency if Assigned LST Depegs - (File: `contracts/oracles/OneETHPriceOracle.sol`)

---

### Summary

`OneETHPriceOracle` unconditionally returns `1e18` for any asset's price. When this oracle is assigned to a Liquid Staking Token (LST) that can trade below 1 ETH, an unprivileged depositor can buy the depegged LST cheaply on the open market and deposit it into `LRTDepositPool` at the hardcoded 1 ETH valuation, receiving more rsETH than the deposited asset is worth. This directly dilutes existing rsETH holders and, in a permanent depeg scenario, causes protocol insolvency.

---

### Finding Description

`OneETHPriceOracle` is a production `IPriceFetcher` implementation that always returns `1e18`:

```solidity
// contracts/oracles/OneETHPriceOracle.sol
function getAssetPrice(address) external pure returns (uint256) {
    return 1e18;
}
``` [1](#0-0) 

This oracle is registered for supported assets via `LRTOracle.updatePriceOracleFor()`: [2](#0-1) 

The registered price is consumed directly in `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`lrtOracle.rsETHPrice()` is a **stored state variable** updated only when `updateRSETHPrice()` is explicitly called — it is not recalculated on every deposit: [4](#0-3) 

The rsETH price itself is computed from `_getTotalEthInProtocol()`, which also calls `getAssetPrice(asset)` for each supported asset: [5](#0-4) 

**Attack scenario:**

1. An LST (e.g., stETH) is assigned `OneETHPriceOracle`. Its market price drops to 0.9 ETH.
2. The stored `rsETHPrice` was last computed when stETH was at 1 ETH — it is now stale and overstated.
3. An attacker buys 1,000 stETH on the open market for 900 ETH.
4. The attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, ...)`.
5. `getAssetPrice(stETH)` returns `1e18` (hardcoded), so `rsethAmountToMint = (1000e18 * 1e18) / rsETHPrice` — the attacker receives rsETH backed by 1,000 ETH of protocol value.
6. The attacker has gained ~100 ETH of rsETH value at the expense of existing rsETH holders.

The `_updateRsETHPrice()` price-guard (`pricePercentageLimit`) only protects against the rsETH price moving too fast — it does not prevent individual asset prices from being wrong, and it does not block deposits: [6](#0-5) 

---

### Impact Explanation

**Critical — Direct theft of user funds / Protocol insolvency.**

- **Short-term depeg**: Attackers arbitrage the price gap, minting rsETH worth more than the deposited collateral. Every such deposit dilutes the ETH backing of all existing rsETH holders, constituting direct theft of their funds.
- **Permanent depeg**: The protocol's TVL is permanently overstated. When users attempt to redeem rsETH, there is insufficient real ETH backing, causing insolvency.

---

### Likelihood Explanation

**Medium.** LST depeg events occur with meaningful frequency (stETH traded at ~0.94 ETH during the Merge period; LUNA/UST demonstrated permanent depeg). The `OneETHPriceOracle` is a deployed production contract explicitly designed for "hard-pegged" assets. Any LST assigned this oracle that subsequently depegs — even temporarily — opens the arbitrage window. No special permissions are required for the exploit; any depositor can trigger it via the public `depositAsset()` function. [7](#0-6) 

---

### Recommendation

Do not use `OneETHPriceOracle` for any LST that can trade below 1 ETH. For every supported LST, use a live price feed (Chainlink, or the LST's own on-chain exchange rate contract) so that a depeg is immediately reflected in deposit calculations. If a 1:1 peg assumption is desired for a specific asset, add a staleness check or a circuit-breaker that pauses deposits when the live price deviates beyond a configurable threshold from 1e18.

---

### Proof of Concept

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
  6. Profit: ~100 ETH extracted from existing rsETH holders
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
