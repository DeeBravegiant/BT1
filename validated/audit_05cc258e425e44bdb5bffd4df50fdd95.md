Audit Report

## Title
`SfrxETHPriceOracle.getAssetPrice()` Returns sfrxETH/frxETH Rate Instead of sfrxETH/ETH, Mispricing sfrxETH When frxETH Depegs - (File: contracts/oracles/SfrxETHPriceOracle.sol)

## Summary
`SfrxETHPriceOracle.getAssetPrice()` returns `sfrxETH.pricePerShare()`, which is the ERC4626 vault exchange rate denominated in frxETH — not ETH. `LRTOracle._getTotalEthInProtocol()` consumes this value as if it were an ETH-denominated price, implicitly assuming frxETH ≡ 1 ETH at all times. When frxETH trades below ETH parity, `rsETHPrice` is inflated relative to the protocol's true ETH-denominated backing, enabling depositors to extract more ETH value than they contributed at the expense of existing rsETH holders.

## Finding Description
`SfrxETHPriceOracle.getAssetPrice()` at line 40 returns `ISfrxETH(sfrxETHContractAddress).pricePerShare()`. The interface comment at line 9 acknowledges this returns "How much frxETH is 1E18 sfrxETH worth" — frxETH, not ETH. sfrxETH is an ERC4626 vault whose underlying asset is frxETH, a synthetic ETH derivative issued by Frax Finance that is pegged but not redeemable 1:1 for ETH on-demand and can trade at a discount.

`LRTOracle._getTotalEthInProtocol()` (lines 336–343) iterates over all supported assets, calls `getAssetPrice(asset)` for each, and multiplies by total deposited amount to compute `totalETHInProtocol`. For sfrxETH, this multiplication uses the frxETH/sfrxETH rate as if it were ETH/sfrxETH. `_updateRsETHPrice()` (line 250) then divides `totalETHInProtocol` by `rsethSupply` to produce `rsETHPrice`.

The `pricePercentageLimit` guard (lines 252–266) checks whether `newRsETHPrice` exceeds `highestRsethPrice` by more than the configured threshold. This guard does not protect against this vulnerability: `pricePerShare()` is an internal ERC4626 accounting rate that does not change when frxETH depegs in secondary markets. The oracle value remains stable while the true ETH value of sfrxETH silently falls, so no price spike is detected and the guard never triggers. The downside protection (lines 270–282) similarly does not trigger because the oracle-reported price does not decrease.

`updateRSETHPrice()` at line 87 is public with no access control, allowing any caller to bake the inflated price into `rsETHPrice`.

## Impact Explanation
**Protocol insolvency (Critical).** When frxETH depegs (e.g., frxETH = 0.95 ETH):
- `getAssetPrice(sfrxETH)` returns `pricePerShare()` ≈ 1.05e18, treated as 1.05 ETH/sfrxETH.
- True ETH value = 1.05 × 0.95 = 0.9975 ETH/sfrxETH.
- `totalETHInProtocol` is overstated by the full depeg magnitude across all sfrxETH holdings.
- `rsETHPrice` is inflated above the true ETH-denominated backing per rsETH.
- A depositor who deposits sfrxETH (worth 0.9975 ETH) receives rsETH priced at 1.05 ETH/sfrxETH, then redeems rsETH for ETH or other LSTs at the inflated rate, extracting more ETH than deposited. The shortfall is borne by existing rsETH holders, constituting direct theft of user funds and protocol insolvency.

## Likelihood Explanation
frxETH has maintained its peg historically but carries no hard 1:1 ETH redemption guarantee. Any Frax protocol stress, liquidity crisis, or market event can cause a depeg — stETH precedent (0.94 ETH in June 2022) demonstrates this is realistic for synthetic ETH derivatives. The structural mispricing is always present; it activates the moment frxETH trades below 1 ETH. No special permissions are required: any user can call `updateRSETHPrice()` and deposit sfrxETH via the public deposit pool.

## Recommendation
Replace the single `pricePerShare()` call with a two-step calculation:

```
sfrxETH/ETH = sfrxETH.pricePerShare() * frxETH/ETH
```

where `frxETH/ETH` is sourced from a Chainlink `frxETH/ETH` feed or a Curve pool TWAP. Alternatively, use a Chainlink `sfrxETH/ETH` feed directly if available, bypassing frxETH entirely. The fix must be applied in `SfrxETHPriceOracle.getAssetPrice()` at line 40.

## Proof of Concept
1. frxETH depegs to 0.95 ETH (market event, no admin action required).
2. Protocol holds 1000 sfrxETH; `pricePerShare()` = 1.05 frxETH/sfrxETH (ERC4626 internal rate, unaffected by market depeg).
3. `SfrxETHPriceOracle.getAssetPrice(sfrxETH)` returns `1.05e18`, treated as ETH by `_getTotalEthInProtocol()`.
4. True ETH value = 1000 × 1.05 × 0.95 = 997.5 ETH. Oracle-reported value = 1000 × 1.05 = 1050 ETH. Overstatement = 52.5 ETH.
5. Attacker calls `updateRSETHPrice()` (public, no access control) — `rsETHPrice` is set to the inflated value.
6. Attacker deposits 100 sfrxETH (true ETH value = 99.75 ETH) and receives rsETH minted at the inflated rate (~105 ETH equivalent).
7. Attacker initiates withdrawal for ETH/stETH, receiving ~105 ETH worth of assets.
8. Net extraction: ~5.25 ETH per 100 sfrxETH deposited, funded by diluting existing rsETH holders.

**Foundry fork test plan:** Fork mainnet, set frxETH/ETH Curve pool price to 0.95 via `vm.mockCall`, call `updateRSETHPrice()`, assert `rsETHPrice` exceeds true ETH-denominated backing per rsETH by ~5%, then simulate deposit + withdrawal to confirm net ETH extraction.