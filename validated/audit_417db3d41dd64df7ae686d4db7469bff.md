Audit Report

## Title
Disabled Downside Circuit-Breaker via Default `pricePercentageLimit == 0` Enables Near-Zero `rsETHPrice` Write and Mint-at-Near-Zero-Price Attack — (`contracts/LRTOracle.sol`, `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`LRTOracle._updateRsETHPrice` contains a downside circuit-breaker that is unconditionally bypassed when `pricePercentageLimit == 0`, which is the default storage value because no initializer sets it. `ChainlinkPriceOracle.getAssetPrice` applies no minimum price floor. When a Chainlink feed reports a severely depegged price, `rsETHPrice` is overwritten with a near-zero value and the protocol is never paused, allowing any subsequent depositor of a correctly-priced asset to mint rsETH at a near-zero denominator and extract value from all existing holders.

## Finding Description

**Root cause 1 — No minimum price floor (`ChainlinkPriceOracle.sol` L49-55)**

`getAssetPrice` casts `price` directly to `uint256` and scales it with no lower-bound check:
```solidity
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```
A feed returning `answer = 1` with `decimals = 8` produces `1e10`, ten orders of magnitude below the expected `~1e18`.

**Root cause 2 — Circuit-breaker gated on `pricePercentageLimit > 0` (`LRTOracle.sol` L270-282)**

```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```
`pricePercentageLimit` is declared at L29 with no initializer. Its default value is `0`. The short-circuit `pricePercentageLimit > 0` makes `isPriceDecreaseOffLimit` permanently `false` regardless of the magnitude of `diff`. The protocol never pauses, and execution falls through to L313 where `rsETHPrice = newRsETHPrice` is written unconditionally.

**Root cause 3 — `updateRSETHPrice()` is permissionlessly callable (`LRTOracle.sol` L87-89)**

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```
Any EOA can commit the near-zero price.

**Root cause 4 — Mint ratio divides by `rsETHPrice` (`LRTDepositPool.sol` L519-520)**

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
With `rsETHPrice = 1e10` and a deposited asset oracle returning `~1e18`, the attacker receives `amount * 1e18 / 1e10 = amount * 1e8` rsETH.

**Why existing checks fail**

`updatePriceOracleForValidated` (`LRTOracle.sol` L101-108) validates `1e16 ≤ price ≤ 1e19` only at oracle registration time. It provides no protection against a price that drops after registration. The `whenNotPaused` guard on `updateRSETHPrice` is irrelevant because the circuit-breaker that would trigger the pause is itself disabled.

## Impact Explanation

An attacker who deposits a small amount of a correctly-priced LST immediately after `rsETHPrice` is set to near-zero receives rsETH representing a disproportionate claim on the entire protocol TVL. Upon redemption, they drain real collateral from all existing rsETH holders. This constitutes **Critical: direct theft of user funds at rest** and potential **protocol insolvency**, both of which are in-scope impacts.

## Likelihood Explanation

- `pricePercentageLimit` defaults to `0` and requires an explicit admin call to `setPricePercentageLimit` to activate the circuit-breaker. Any deployment that omits this step is permanently vulnerable.
- LST depegging events are historically documented (stETH, cbETH, rETH). A Chainlink feed correctly reporting a severely depegged price is a realistic, non-adversarial trigger and does not require oracle operator compromise.
- `updateRSETHPrice()` is public; the attacker controls the timing of the price commit.
- `minRSETHAmountExpected` can be set to `0` by the attacker, removing the only depositor-side slippage guard.

## Recommendation

1. **Remove the `pricePercentageLimit > 0` guard** from the downside circuit-breaker in `_updateRsETHPrice`. The pause should trigger unconditionally when the price drop exceeds a hard-coded minimum threshold, independent of whether `pricePercentageLimit` has been configured.
2. **Initialize `pricePercentageLimit` to a non-zero value** in the `initialize` function (e.g., `1e16` = 1%).
3. **Add a minimum price floor in `ChainlinkPriceOracle.getAssetPrice`**: revert if the returned price is below a configurable threshold (e.g., `0.5e18`).
4. **Add a staleness/sanity check in `_updateRsETHPrice`**: revert if `newRsETHPrice < previousPrice * MIN_PRICE_RATIO` rather than silently writing the bad price.

## Proof of Concept

```solidity
// Foundry fork/unit test

// 1. Deploy protocol; pricePercentageLimit is never set → defaults to 0
// 2. Alice deposits 1000 stETH → rsETHPrice ≈ 1e18

// 3. Mock Chainlink feed for stETH returns answer = 1, decimals = 8
//    → ChainlinkPriceOracle.getAssetPrice(stETH) = 1e10

// 4. Attacker calls LRTOracle.updateRSETHPrice()
//    → totalETHInProtocol ≈ 1000e18 * 1e10 / 1e18 = 1000e10
//    → newRsETHPrice = 1000e10 / rsethSupply ≈ 1e10
//    → pricePercentageLimit == 0 → isPriceDecreaseOffLimit = false → no pause
//    → rsETHPrice = 1e10  ✓

// 5. Attacker deposits 1 rETH (oracle still returns ~1e18 for rETH)
//    rsethAmountToMint = (1e18 * 1e18) / 1e10 = 1e26
//    Attacker receives 1e26 rsETH for 1 rETH

// 6. Assertions
assertGt(rsETH.balanceOf(attacker), rsETH.balanceOf(alice) * 1e6);
assertLt(lrtDepositPool.getTotalAssetDeposits(rETH_addr), 2e18);
// Attacker redeems 1e26 rsETH, draining Alice's 1000 stETH collateral
```