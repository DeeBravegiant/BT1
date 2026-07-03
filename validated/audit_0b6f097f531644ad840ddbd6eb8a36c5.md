Audit Report

## Title
No Staleness Check on `CrossChainRateReceiver.getRate()` Allows Minting Excess rsETH at Stale Rate — (File: contracts/cross-chain/CrossChainRateReceiver.sol)

## Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally, never consulting the `lastUpdated` timestamp. Because `updateRate()` on the L1 provider is permissionless but requires the caller to supply ETH for LayerZero fees with no reimbursement, the L2 rate can drift stale indefinitely. Any depositor can exploit the stale (lower) rate to receive more rsETH than their ETH contribution is worth at the current true exchange rate, extracting accrued yield from existing rsETH holders.

## Finding Description

`CrossChainRateReceiver.getRate()` at lines 102–105 returns `rate` with no staleness guard:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
```

`lastUpdated` is written in `lzReceive()` (line 97) but is never read anywhere in the contract. `RSETHPoolV3.viewSwapRsETHAmountAndFee()` (lines 299–308) calls `getRate()` to obtain `rsETHToETHrate` and computes `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`. A stale (lower) rate inflates the rsETH minted. `MultiChainRateProvider.updateRate()` (lines 108–137) is permissionless but requires the caller to pay LayerZero messaging fees in ETH with no on-chain reimbursement, creating a gap during which the L2 rate lags the true L1 `rsETHPrice`. No existing check in any pool contract (`RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) validates oracle freshness before minting.

## Impact Explanation

**High — Theft of unclaimed yield.** When rsETH accrues staking yield on L1, `rsETHPrice` rises but the L2 receiver's `rate` remains frozen. A depositor mints `amountAfterFee * 1e18 / staleRate` rsETH; because `staleRate < trueRate`, the excess rsETH represents yield that belongs to existing rsETH holders. Upon bridging and redeeming on L1 at the true rate, the attacker extracts ETH that was never theirs. The magnitude scales linearly with both the staleness duration and deposit size, and the attack is repeatable.

## Likelihood Explanation

**Medium.** No privileged access is required. The only precondition is that the L2 rate has drifted below the true L1 rate, which occurs naturally whenever no one voluntarily pays LayerZero fees to refresh it. An attacker can passively monitor the on-chain divergence between `LRTOracle.rsETHPrice` on L1 and `CrossChainRateReceiver.rate` on L2, then deposit precisely when the gap is largest. The attack is front-runnable and repeatable across every L2 deployment.

## Recommendation

Add a configurable `MAX_STALENESS` constant and revert in `getRate()` if the rate is too old:

```solidity
uint256 public constant MAX_STALENESS = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_STALENESS, "Rate is stale");
    return rate;
}
```

Pair this with an on-chain keeper incentive (e.g., a small ETH reward funded from protocol fees) to ensure `updateRate()` is called promptly. This converts silent mis-pricing into a hard revert, preventing deposits when the oracle has not been refreshed.

## Proof of Concept

1. At T₀: `LRTOracle.rsETHPrice = 1.05e18`; L2 `CrossChainRateReceiver.rate = 1.05e18`.
2. rsETH accrues yield; at T₁: `LRTOracle.rsETHPrice = 1.10e18`. No one calls `updateRate()` (LayerZero fees are non-trivial, no reimbursement).
3. L2 `CrossChainRateReceiver.rate` remains `1.05e18`.
4. Attacker calls `RSETHPoolV3.deposit{value: 10 ether}("")`:
   - `viewSwapRsETHAmountAndFee(10e18)` → `rsETHAmount = 10e18 * 1e18 / 1.05e18 ≈ 9.524e18` rsETH minted.
   - Correct amount at true rate: `10e18 * 1e18 / 1.10e18 ≈ 9.091e18` rsETH.
   - Excess: `≈ 0.433e18` rsETH.
5. Attacker bridges rsETH to L1 and redeems at `1.10e18` rate, receiving `≈ 10.476 ETH`.
6. Profit: `≈ 0.476 ETH` per 10 ETH deposited, extracted from existing rsETH holders.

**Foundry fork test plan:** Fork an L2 deployment; set `CrossChainRateReceiver.rate` to `1.05e18` and `lastUpdated` to `block.timestamp - 2 days`; call `RSETHPoolV3.deposit{value: 10 ether}("")`; assert minted rsETH exceeds `10e18 * 1e18 / 1.10e18`; confirm no revert occurs.