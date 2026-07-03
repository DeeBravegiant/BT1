Audit Report

## Title
Missing Staleness Check in `getRate()` Enables Over-Issuance of rsETH When LayerZero Pipeline Stalls — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally, never consulting the `lastUpdated` timestamp that is written on every `lzReceive` call. If the LayerZero update pipeline stalls while the true L1 rsETH/ETH rate appreciates, any depositor calling `RSETHPoolV3.deposit()` receives more rsETH than the current backing warrants, diluting the accrued yield of existing holders.

## Finding Description
`CrossChainRateReceiver.getRate()` (L103–105) returns `rate` with no staleness guard:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
```

`lastUpdated` is written on every `lzReceive` call (L95–97):

```solidity
rate = _rate;
lastUpdated = block.timestamp;
```

but is never read inside `getRate()`. The contract stores all data needed for a freshness check and deliberately omits it.

`RSETHPoolV3.getRate()` (L235–237) delegates directly to this oracle:

```solidity
return IOracle(rsETHOracle).getRate();
```

`viewSwapRsETHAmountAndFee` (L299–308) uses the returned rate as the denominator:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

`deposit()` (L258–262) mints `rsETHAmount` directly from this calculation:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
wrsETH.mint(msg.sender, rsETHAmount);
```

When `stale_rate < true_rate`, the division yields a larger `rsETHAmount` than the true rate would produce. The minted excess is unbacked by ETH, diluting the proportional yield share of all existing rsETH holders.

## Impact Explanation
**High — Theft of unclaimed yield.** rsETH is a yield-bearing token whose value accrues continuously on L1. When the stored rate lags the true rate, every deposit mints excess rsETH that is not backed by additional ETH. Because rsETH value is shared proportionally across all holders, the over-issuance directly transfers accrued-but-unclaimed yield from existing holders to the depositor. The daily mint limit caps per-day damage but does not prevent the exploit within that window.

## Likelihood Explanation
LayerZero pipeline stalls are a realistic operational scenario: unpaid relayer/oracle fees, LZ network congestion, or provider-side outages can halt `lzReceive` calls for hours or days. No admin compromise, key leak, or governance capture is required. The attacker only needs to call the public `deposit()` function while the rate is stale. rsETH accrues yield continuously on L1, so even a multi-hour stall creates exploitable divergence. The attack is repeatable up to the daily mint limit each day the stall persists.

## Recommendation
Add a configurable `maxStaleness` parameter and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This uses the already-stored `lastUpdated` variable and requires no structural changes to the contract.

## Proof of Concept
```solidity
function testStaleRateYieldTheft() public {
    // 1. Record current stale rate (e.g. 1.05e18), do NOT call lzReceive
    uint256 staleRate = receiver.rate(); // 1.05e18

    // 2. Simulate LZ pipeline stall: advance time without updating rate
    vm.warp(block.timestamp + 7 days);

    // 3. True rate on L1 has appreciated to 1.08e18, but receiver.rate() still returns 1.05e18

    // 4. Attacker deposits 1 ETH
    vm.deal(attacker, 1 ether);
    vm.prank(attacker);
    pool.deposit{value: 1 ether}("ref");

    // 5. Assert attacker received more rsETH than true rate warrants
    uint256 received = wrsETH.balanceOf(attacker);
    uint256 expectedAtTrueRate = 1 ether * 1e18 / 1.08e18;
    assertGt(received, expectedAtTrueRate, "Attacker minted excess rsETH");

    // 6. Excess = received - expectedAtTrueRate, representing stolen yield
}
```