Audit Report

## Title
Stale Cross-Chain Rate Used Without Staleness Validation Allows Over-Minting of wrsETH - (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver` stores a `lastUpdated` timestamp on every LayerZero rate update but `getRate()` returns the stored `rate` unconditionally without any staleness check. L2 deposit pools (`RSETHPoolV2`, `RSETHPoolV3`, and their variants) call `getRate()` to compute how many wrsETH tokens to mint per unit of ETH deposited. When the L1 rsETH price appreciates and the L2 rate lags behind, any unprivileged depositor can exploit the stale (artificially low) rate to receive more wrsETH than the current exchange rate warrants, diluting the redemption value available to all existing wrsETH holders.

## Finding Description

`CrossChainRateReceiver.lzReceive()` writes both `rate` and `lastUpdated` on every inbound LayerZero message:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L95-97
rate = _rate;
lastUpdated = block.timestamp;   // written but never read again
```

`getRate()` ignores `lastUpdated` entirely and returns the stored value unconditionally:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L103-105
function getRate() external view returns (uint256) {
    return rate;
}
```

Every L2 deposit pool delegates pricing to this function. In `RSETHPoolV2`:

```solidity
// contracts/pools/RSETHPoolV2.sol L201-203
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
```

```solidity
// contracts/pools/RSETHPoolV2.sol L225-234
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // stale rate accepted silently
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The identical pattern exists in `RSETHPoolV3` (L299-308), `RSETHPoolV2ExternalBridge`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`. Because `rsETHAmount` is inversely proportional to `rsETHToETHrate`, a stale rate lower than the true current rate causes every pool to mint more wrsETH per ETH than it should. A grep across all contracts confirms `lastUpdated` is only ever written — it is never read back to gate any operation.

The `dailyMintLimit` modifier in both pool versions caps the total wrsETH minted per day but does not prevent the attack; it only bounds the per-day damage. The `paused` flag requires privileged action to set and cannot react automatically to a stale rate.

## Impact Explanation

**High — Theft of unclaimed yield.**

When the L1 rsETH price appreciates and the L2 rate has not yet been refreshed, an attacker deposits ETH at the stale lower rate and receives a proportionally larger wrsETH balance. Upon redemption after the rate is updated, that wrsETH is redeemable for more rsETH than the deposited ETH warranted at the true rate. The surplus is drawn from the pool's rsETH reserves, directly reducing the redemption value available to all other wrsETH holders. The stolen value is the yield/appreciation that existing holders had accrued — fitting the "Theft of unclaimed yield" impact class. The attack does not require any privileged access and is repeatable across every deployed pool variant.

## Likelihood Explanation

LayerZero message delivery is not instantaneous, and `updateRate()` must be called manually (or by a keeper) on the provider side. Any period during which the L1 rsETH price rises faster than the cross-chain update cadence creates an exploitable window. An attacker needs only to compare the L1 oracle price against the L2 stored `rate` and deposit when the gap is profitable. No privileged access, flash loan, or governance action is required; `deposit()` is fully public on all pool variants. The condition (rate lag during price appreciation) is a normal operational state, not an edge case.

## Recommendation

Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours, set by owner

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

Alternatively, expose a `isStale()` view and have the pool's `deposit()` revert when the oracle is stale, or integrate automatic pausing when the rate has not been refreshed within the expected window. The `maxStaleness` value should be set conservatively relative to the keeper's update cadence.

## Proof of Concept

1. At T₀, L1 rsETH price = 1.05 ETH. LayerZero delivers this rate; `CrossChainRateReceiver.rate = 1.05e18`, `lastUpdated = T₀`.
2. At T₁ = T₀ + 48 h, L1 rsETH price rises to 1.10 ETH. No `updateRate()` call has been made; L2 `rate` is still `1.05e18`.
3. Attacker calls `RSETHPoolV2.deposit{value: 100 ether}("")`.
4. `viewSwapRsETHAmountAndFee(100 ether)` computes `rsETHAmount = 100e18 * 1e18 / 1.05e18 ≈ 95.24 wrsETH`.
5. Correct amount at current rate: `100e18 * 1e18 / 1.10e18 ≈ 90.91 wrsETH`.
6. Attacker receives **≈ 4.33 excess wrsETH** (≈ 4.76% over-mint) per 100 ETH deposited.
7. After the rate updates to 1.10, attacker redeems 95.24 wrsETH for ≈ 104.76 ETH worth of rsETH, profiting at the expense of other holders.

**Foundry fork test plan:**
- Fork an L2 where `CrossChainRateReceiver` is deployed.
- Record `rate` and `lastUpdated`.
- `vm.warp(block.timestamp + 48 hours)` to simulate staleness.
- Call `RSETHPoolV2.deposit{value: 100 ether}("")` as an unprivileged address.
- Assert `wrsETH.balanceOf(attacker) > 100e18 * 1e18 / trueCurrentRate`.
- Simulate rate update via `lzReceive` with the new higher rate.
- Assert attacker's wrsETH redeems for more ETH-equivalent rsETH than deposited.