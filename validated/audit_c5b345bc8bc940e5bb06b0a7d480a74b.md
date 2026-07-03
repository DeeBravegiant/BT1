All five code references check out exactly against the live source. Let me confirm each step before rendering the verdict.

Audit Report

## Title
Stale `ethValueInWithdrawal` Snapshot Causes rsETH NAV Mispricing When Oracle Price Moves While Assets Are in Converter - (File: contracts/LRTConverter.sol)

## Summary

`LRTConverter.ethValueInWithdrawal` is written once at the oracle price captured at the moment of `transferAssetFromDepositPool` and is never revalued. Because `getAssetDistributionData` hard-codes `assetLyingInConverter = 0` for every non-ETH asset and `getETHDistributionData` returns the stale snapshot as `ethLyingInConverter`, any stETH sitting in the converter is permanently valued at the transfer-time price P1 rather than the current price P2 for the entire duration it remains there. `updateRSETHPrice` is public and can be called by anyone to commit that mispriced TVL into the stored `rsETHPrice`, enabling an unprivileged attacker to deposit or redeem at a manipulated price.

## Finding Description

**Step 1 — Snapshot written once at transfer time, never revalued.**

In `transferAssetFromDepositPool`, the line:

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

records the ETH value of the transferred asset at the oracle price P1 at that moment. No subsequent revaluation occurs anywhere in the contract. [1](#0-0) 

**Step 2 — Non-ETH asset contribution zeroed in distribution data.**

`getAssetDistributionData` explicitly sets `assetLyingInConverter = 0` for every non-ETH asset, with the comment that the converter balance is "accounted in their eth value" via `getETHDistributionData`. This means X stETH physically held by the converter is invisible to `getTotalAssetDeposits(stETH)`. [2](#0-1) 

**Step 3 — Stale snapshot injected into ETH distribution.**

`getETHDistributionData` returns the stored snapshot directly as `ethLyingInConverter`:

```solidity
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [3](#0-2) 

**Step 4 — TVL computed with mixed prices.**

`_getTotalEthInProtocol` multiplies each asset's `getTotalAssetDeposits` by the **current** oracle price. For stETH, the converter balance is excluded (zero), so X stETH is never multiplied by P2. For ETH, the snapshot `X*P1/1e18` is added as a raw ETH amount (multiplied by ETH price ≈ 1e18). Net effect: the X stETH is valued at P1, not P2, producing a TVL that diverges from true NAV by `X * |P2 − P1| / 1e18` ETH. [4](#0-3) 

**Step 5 — Public price update commits the mispriced TVL.**

`updateRSETHPrice()` is `public whenNotPaused` with no role restriction. Any caller can commit the mispriced TVL into `rsETHPrice`. [5](#0-4) 

**Why existing guards are insufficient.**

`_updateRsETHPrice` contains a `pricePercentageLimit` guard that reverts for non-managers if the new price exceeds `highestRsethPrice` by more than the configured threshold, and pauses the protocol if the price drops too far below `highestRsethPrice`. [6](#0-5) 

This guard is insufficient for two reasons: (1) `pricePercentageLimit` is configurable and defaults to 0, disabling all threshold checks entirely; (2) even when set, it only blocks moves exceeding the threshold — small oracle moves (0.01–0.1 %) that are well within any reasonable threshold pass through unimpeded, yet still produce a measurable and exploitable NAV discrepancy at scale (e.g., ~10 ETH for a 10,000 stETH transfer at 0.1 % drift).

## Impact Explanation

**Price-increase scenario (P2 > P1):** TVL is under-counted by `X*(P2−P1)/1e18` ETH. `rsETHPrice` is set below true NAV. An attacker deposits at the depressed price, receives more rsETH than NAV warrants. Once accounting corrects (assets returned or ETH claimed and `ethValueInWithdrawal` zeroed), `rsETHPrice` rises and the attacker redeems at a profit, extracting yield that belonged to existing holders. This is **Theft of unclaimed yield — High**.

**Price-decrease scenario (P2 < P1):** TVL is over-counted by `X*(P1−P2)/1e18` ETH. `rsETHPrice` is set above true NAV. An attacker redeems rsETH at the inflated price, receiving more ETH than their proportional share — a direct loss to remaining holders. This escalates to **Direct theft of user funds — Critical**, though the price-increase upside check provides partial mitigation for large moves when `pricePercentageLimit > 0`.

## Likelihood Explanation

- `transferAssetFromDepositPool` is a routine operational call expected to be executed regularly as the protocol unstakes stETH via Lido; no compromise of any role is required — the attacker only needs to observe the on-chain event.
- stETH/ETH oracle price moves continuously; even sub-0.1 % moves over the hours-to-days window that assets sit in the converter produce a measurable gap at protocol scale.
- `updateRSETHPrice` is public; the attacker can call it at will to commit the mispriced TVL before depositing or after withdrawing.
- No front-running of admin transactions is required; the attacker only needs to observe on-chain state and time two public calls (`updateRSETHPrice` + `depositAsset` / `requestWithdrawal`).

## Recommendation

Replace the static snapshot with a live revaluation in `getETHDistributionData`. For each non-ETH supported asset, compute `IERC20(asset).balanceOf(lrtConverter) * oracle.getAssetPrice(asset) / 1e18` at query time and sum these into `ethLyingInConverter`. The stored `ethValueInWithdrawal` should be reduced to cover only the portion already submitted to Lido's withdrawal queue (where the ERC-20 token no longer exists on-chain); the portion still held as ERC-20 tokens must be revalued dynamically. Correspondingly, `transferAssetFromDepositPool` should only increment `ethValueInWithdrawal` at the moment `unstakeStEth` is called (i.e., when the NFT is minted and the token leaves the converter's balance), not at the moment of transfer from the deposit pool.

## Proof of Concept

Minimal reproducible call sequence on a mainnet fork:

1. Deploy/fork the protocol with 10,000 stETH in the deposit pool.
2. Record `P1 = lrtOracle.getAssetPrice(stETH)`.
3. As `ASSET_TRANSFER_ROLE`, call `lrtConverter.transferAssetFromDepositPool(stETH, 10_000e18)`. `ethValueInWithdrawal` is now `10_000e18 * P1 / 1e18`.
4. Advance the mock oracle price to `P2 = P1 * 1001 / 1000` (+0.1 %).
5. As attacker (unprivileged EOA), call `lrtOracle.updateRSETHPrice()`. Observe `rsETHPrice` is now below true NAV by `10_000e18 * (P2 − P1) / 1e18 ≈ 10 ETH` worth of TVL.
6. Attacker calls `lrtDepositPool.depositETH{value: 100 ether}(0, "")`, receiving more rsETH than NAV warrants.
7. As `ASSET_TRANSFER_ROLE`, call `lrtConverter.transferAssetToDepositPool(stETH, 10_000e18)`. `ethValueInWithdrawal` is decremented at the **current** price P2, zeroing or nearly zeroing the snapshot.
8. Attacker calls `lrtOracle.updateRSETHPrice()`. Price corrects upward.
9. Assert `correctedPrice > depressedPrice` and that attacker's rsETH is now worth more ETH than deposited — invariant `rsETH_supply * rsETHPrice == true_TVL` is violated at step 5.

### Citations

**File:** contracts/LRTConverter.sol (L140-142)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
```

**File:** contracts/LRTDepositPool.sol (L460-461)
```text
        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L498-500)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-282)
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
        }

        // downside protection — pause if price drops too far
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
