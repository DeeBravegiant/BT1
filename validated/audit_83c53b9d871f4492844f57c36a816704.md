Audit Report

## Title
L2 Pool Depositors Receive Excess wrsETH When Oracle Rate Is Stale-Low — (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

## Summary
`RSETHPoolV3ExternalBridge`, `RSETHPoolV3`, and `RSETHPoolNoWrapper` all compute the wrsETH/rsETH amount to issue using the live oracle rate at deposit time with no staleness guard. When the L2 oracle rate lags the true rsETH/ETH rate — a realistic condition given cross-chain message delays — a depositor receives more wrsETH than the deposited ETH can back at the true rate. The ETH is later bridged to L1 and converted to rsETH at the true (higher) rate, leaving the wrsETH wrapper undercollateralised and stealing yield from all other wrsETH holders.

## Finding Description
Every L2 pool's deposit path resolves to `viewSwapRsETHAmountAndFee`, which divides the post-fee ETH amount by the live oracle rate with no staleness check:

```solidity
// RSETHPoolV3ExternalBridge.sol lines 418-427
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();   // no staleness check
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

`getRate()` simply forwards to the configured `rsETHOracle` with no freshness validation:

```solidity
// RSETHPoolV3ExternalBridge.sol lines 355-357
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
```

The L2 oracle is `CrossChainRateReceiver`, which stores a `lastUpdated` timestamp but whose `getRate()` returns the stored `rate` without checking it:

```solidity
// CrossChainRateReceiver.sol lines 103-105
function getRate() external view returns (uint256) {
    return rate;
}
```

The rate is only updated when a LayerZero message arrives from L1. If that message is delayed (network congestion, relayer downtime, etc.), the stored rate can lag the true L1 rsETH/ETH rate for an extended period. Because the rsETH/ETH rate accrues monotonically from staking rewards, the L2 oracle rate is routinely below the true rate during any update gap.

The minted amount is then issued atomically:

```solidity
// RSETHPoolV3ExternalBridge.sol line 381
wrsETH.mint(msg.sender, rsETHAmount);
```

The same pattern is confirmed in `RSETHPoolV3.sol` (lines 299-308) and `RSETHPoolNoWrapper.sol` (lines 277-286).

The L1 `LRTOracle` has explicit downside protection — if the computed rsETH price falls too far below `highestRsethPrice`, it pauses `LRTDepositPool` and `LRTWithdrawalManager` (lines 270-281). No equivalent circuit-breaker exists in any L2 pool contract.

The `limitDailyMint` modifier (lines 130-159) limits total daily wrsETH issuance but does not prevent the rate-mismatch exploit within that cap; it only bounds the maximum loss per day.

## Impact Explanation
**High — Theft of unclaimed yield from existing wrsETH holders.**

When the L2 oracle rate is stale-low (e.g., shows 1.00 ETH/rsETH while the true rate is 1.05 ETH/rsETH):
- Attacker deposits 100 ETH → receives `100 / 1.00 = 100 wrsETH`.
- Fair issuance at true rate: `100 / 1.05 ≈ 95.24 wrsETH`.
- ETH is bridged to L1 and deposited into `LRTDepositPool`, yielding `100 / 1.05 ≈ 95.24 rsETH` to back the wrapper.
- The wrapper now has 100 wrsETH outstanding but only 95.24 rsETH backing — a shortfall of ≈4.76 rsETH.
- The 4.76 rsETH shortfall is socialised across all other wrsETH holders, permanently diluting their claims. This constitutes theft of unclaimed yield from existing holders.

## Likelihood Explanation
**Medium.** The L2 oracle (`CrossChainRateReceiver`) is updated via LayerZero messages from L1. Any relayer delay, network congestion, or message queue backup creates a staleness window. Because the rsETH/ETH rate increases monotonically, the oracle is structurally biased to lag the true rate. An attacker needs only to compare the on-chain L2 oracle rate against the publicly readable L1 `LRTOracle.rsETHPrice()` and call `deposit()` when a gap exists. No privileged access, no front-running of a specific transaction, and no external protocol compromise is required.

## Recommendation
1. **Short term**: Add a staleness check in `getRate()` or `viewSwapRsETHAmountAndFee` that reverts if `CrossChainRateReceiver.lastUpdated` is older than an acceptable threshold (e.g., 25 hours or a configurable `maxStaleness`).
2. **Short term**: Add a `minRateExpected` parameter to `deposit()` so the protocol can enforce a floor rate, analogous to the `minRSETHAmountExpected` slippage guard in `LRTDepositPool.depositAsset`.
3. **Long term**: Mirror the L1 `pricePercentageLimit` circuit-breaker on L2: if the oracle rate drops more than a configured percentage below its historical high, pause L2 deposits automatically.

## Proof of Concept
**Setup**: L2 `CrossChainRateReceiver.rate` = `1.00e18` (stale, last updated 20+ hours ago). True rsETH/ETH rate on L1 is `1.05e18`.

**Step 1** — Attacker calls `RSETHPoolV3ExternalBridge.deposit{value: 100 ether}("")`.

`viewSwapRsETHAmountAndFee(100e18)`:
```
fee = 0 (feeBps = 0)
rsETHAmount = 100e18 * 1e18 / 1.00e18 = 100e18   // 100 wrsETH minted
```

**Step 2** — `wrsETH.mint(attacker, 100e18)` executes. Attacker holds 100 wrsETH.

**Step 3** — Bridger calls `bridgeAssets(...)`. 100 ETH is sent to L1 `L1Vault`, which calls `LRTDepositPool.depositETH`. At the true rate of 1.05 ETH/rsETH, `getRsETHAmountToMint` yields:
```
rsethAmountToMint = 100e18 * 1e18 / 1.05e18 ≈ 95.24e18   // rsETH minted to wrapper
```

**Step 4** — Wrapper now holds ≈95.24 rsETH backing 100 wrsETH. Shortfall: ≈4.76 rsETH, socialised across all other wrsETH holders.

**Step 5** — Oracle updates to `1.05e18`. Attacker redeems 100 wrsETH for ≈100 rsETH (≈105 ETH). Net profit: ≈5 ETH extracted from other wrsETH holders.

A Foundry fork test can reproduce this by: (1) forking the L2 chain, (2) manipulating `CrossChainRateReceiver.rate` to a stale-low value via the owner, (3) calling `deposit()`, (4) simulating the bridge flow to L1 at the true rate, and (5) asserting that `rsETH.balanceOf(wrapper) < wrsETH.totalSupply()`.