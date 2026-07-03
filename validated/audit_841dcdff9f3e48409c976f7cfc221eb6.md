The code confirms all claims. Let me verify the key facts:

1. `rsETHPrice` is a stored state variable at [1](#0-0) 

2. `updatePriceOracleFor()` writes the new oracle but never calls `updateRSETHPrice()` or `_updateRsETHPrice()`: [2](#0-1) 

3. `updatePriceOracleForValidated()` similarly only delegates to `updatePriceOracleFor()` without refreshing the price: [3](#0-2) 

4. `getRsETHAmountToMint()` divides the live oracle price by the stale stored `rsETHPrice`: [4](#0-3) 

5. `updateRSETHPrice()` is public but not called atomically inside the oracle update path: [5](#0-4) 

The exploit path is reachable by any unprivileged depositor after a legitimate admin oracle update, with no special capability required. The math is correct: a stale denominator inflates rsETH minted, diluting existing holders' unclaimed yield.

---

Audit Report

## Title
Stale `rsETHPrice` After Oracle Update Enables Over-Minting of rsETH, Diluting Existing Holders - (File: `contracts/LRTOracle.sol`)

## Summary
`updatePriceOracleFor()` and `updatePriceOracleForValidated()` replace the live price feed for a supported asset but never refresh the stored `rsETHPrice` state variable. Because `LRTDepositPool.getRsETHAmountToMint()` divides the live new-oracle asset price by the stale stored `rsETHPrice`, any deposit executed between the oracle swap and the next explicit `updateRSETHPrice()` call mints an incorrect number of rsETH shares, stealing unclaimed yield from existing holders when the new oracle returns a higher price.

## Finding Description
`LRTOracle` stores the rsETH/ETH exchange rate in `rsETHPrice` (L28), which is only updated when `_updateRsETHPrice()` is explicitly invoked. `updatePriceOracleFor()` (L113–119) writes `assetPriceOracle[asset] = priceOracle` and emits an event, but does not call `_updateRsETHPrice()`. `updatePriceOracleForValidated()` (L101–108) performs a sanity-range check on the new oracle's price and then delegates to `updatePriceOracleFor()`, also without refreshing `rsETHPrice`.

After the oracle swap, `getAssetPrice(asset)` (L156–158) immediately returns the new oracle's live price, while `rsETHPrice()` still returns the value computed under the old oracle. `getRsETHAmountToMint()` (L516–521) computes:

```
rsethAmountToMint = (amount × lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()
```

The numerator is live; the denominator is stale. No guard in the deposit path (`_beforeDeposit`, `depositAsset`, `depositETH`) detects or prevents this inconsistency. The `minRSETHAmountExpected` slippage parameter protects only the depositor, not existing holders.

## Impact Explanation
**High — Theft of unclaimed yield.**

When the new oracle returns a higher asset price than the old one, the stale `rsETHPrice` denominator is too small, causing `getRsETHAmountToMint()` to return more rsETH than the depositor is entitled to. The surplus rsETH represents a claim on protocol assets that rightfully belongs to existing rsETH holders as accrued (but not yet distributed) yield. Concretely: if stETH oracle moves from 1.00 ETH to 1.05 ETH, a 100 stETH deposit mints 105 rsETH instead of 100, extracting 5 rsETH of value from existing holders at zero cost beyond the deposit itself. The attack is repeatable for every deposit in the staleness window and scales linearly with deposit size.

## Likelihood Explanation
Oracle updates are routine operational events (feed migrations, adapter upgrades). The attacker requires no privileged access — only the ability to call `depositAsset()` or `depositETH()` after the `AssetPriceOracleUpdate` event is emitted and before `updateRSETHPrice()` is called. `updateRSETHPrice()` is a separate public transaction with no atomicity guarantee relative to the oracle update. The window may span multiple blocks. Mempool monitoring is sufficient to detect the opportunity, but even passive depositors in the same block benefit from the over-mint.

## Recommendation
`updatePriceOracleFor()` should call `_updateRsETHPrice()` immediately after writing the new oracle address, so `rsETHPrice` is always consistent with the currently registered oracles before any deposit can use the new price feed:

```solidity
function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
    if (lrtConfig.isSupportedAsset(asset)) {
        UtilLib.checkNonZeroAddress(priceOracle);
    }
    assetPriceOracle[asset] = priceOracle;
    emit AssetPriceOracleUpdate(asset, priceOracle);
    _updateRsETHPrice(); // atomically refresh stored price
}
```

`updatePriceOracleForValidated()` already delegates to `updatePriceOracleFor()`, so it inherits the fix automatically.

## Proof of Concept
**Initial state:** stETH oracle returns 1.00 ETH/stETH; `rsETHPrice = 1.00e18`; rsETH total supply = 1 000 rsETH backed by 1 000 stETH.

1. Admin calls `updatePriceOracleFor(stETH, newOracle)` where `newOracle.getAssetPrice(stETH)` returns `1.05e18`. `rsETHPrice` remains `1.00e18`.
2. Attacker calls `depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` = `(100e18 × 1.05e18) / 1.00e18` = **105 rsETH**.
4. Correct mint (after price refresh) = `(100e18 × 1.05e18) / 1.05e18` = **100 rsETH**.
5. The 5 surplus rsETH represent ~0.48% dilution of the existing 1 000 rsETH holders, extracted at zero cost.
6. When `updateRSETHPrice()` is eventually called, `rsETHPrice` rises to ~1.05e18, but the attacker already holds the over-minted shares.

**Foundry test plan:**
- Deploy `LRTOracle` and `LRTDepositPool` with a mock stETH price oracle returning `1.00e18`.
- Seed the pool with 1 000 stETH; call `updateRSETHPrice()` to set `rsETHPrice = 1.00e18`.
- Deploy a second mock oracle returning `1.05e18`; call `updatePriceOracleFor(stETH, newOracle)`.
- Call `getRsETHAmountToMint(stETH, 100e18)` and assert it returns `105e18` (demonstrating the bug).
- Call `updateRSETHPrice()`, then call `getRsETHAmountToMint(stETH, 100e18)` again and assert it returns `100e18` (demonstrating the correct post-fix behavior).

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
