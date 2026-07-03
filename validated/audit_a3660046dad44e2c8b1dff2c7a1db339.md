Audit Report

## Title
Missing Staleness Check in `getRate()` Allows Excess wrsETH Minting at Stale Cross-Chain Rate - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

## Summary

`CrossChainRateReceiver` records `lastUpdated` on every LayerZero rate message but `getRate()` returns the stored `rate` unconditionally with no freshness guard. All three L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolNoWrapper`) call `getRate()` to compute mint amounts. When the cross-chain rate is stale, any depositor receives more wrsETH/rsETH than the deposited ETH is worth, diluting existing holders' accumulated yield.

## Finding Description

`CrossChainRateReceiver.lzReceive()` sets `lastUpdated = block.timestamp` each time a rate arrives from L1, but `getRate()` ignores it entirely:

```solidity
// CrossChainRateReceiver.sol L97
lastUpdated = block.timestamp;   // recorded but never read

// CrossChainRateReceiver.sol L103-105
function getRate() external view returns (uint256) {
    return rate;                 // no staleness check
}
```

All three pool variants delegate to this function for deposit pricing:

- `RSETHPoolV3.viewSwapRsETHAmountAndFee()` — `rsETHAmount = amountAfterFee * 1e18 / getRate()`
- `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee()` — identical formula
- `RSETHPoolNoWrapper.viewSwapRsETHAmountAndFee()` — identical formula

`MultiChainRateProvider.updateRate()` is permissionless but requires the caller to supply ETH for LayerZero messaging fees. It is not called automatically; it depends on a keeper or an external caller. Any gap between keeper runs leaves the L2 rate stale.

rsETH's exchange rate monotonically increases as EigenLayer staking rewards accumulate. A stale (lower-than-current) rate makes the denominator in the mint formula smaller than it should be, so `rsETHAmount` is inflated. The daily mint limit (`limitDailyMint`) caps total daily volume but does not prevent minting at a wrong rate within that cap.

## Impact Explanation

**High — Theft of unclaimed yield.**

The rate increase since the last update represents yield that has accrued to existing wrsETH holders. A depositor who mints during a stale window captures a portion of that yield: they receive more wrsETH than their ETH warrants. When the pool's ETH is eventually bridged to L1 and converted to rsETH, the rsETH minted is less than the outstanding wrsETH, leaving existing holders' shares undercollateralised. The shortfall is exactly the yield stolen. This is a direct, concrete, on-chain economic loss to existing holders, not a hypothetical.

## Likelihood Explanation

`updateRate()` requires ETH payment for LayerZero fees and is not self-executing. Keeper downtime, LayerZero congestion, or simply a gap between keeper runs creates exploitable windows. The protocol is deployed on at least five L2s (Arbitrum, Optimism, Base, Linea, Unichain), multiplying the number of independent staleness windows. Any unprivileged depositor can read `CrossChainRateReceiver.lastUpdated` on-chain and time a deposit to coincide with a stale window. No special access or capital beyond the deposit itself is required.

## Recommendation

Add a configurable maximum staleness threshold to `getRate()` in `CrossChainRateReceiver`:

```solidity
uint256 public maxStaleness = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This causes all pool `deposit()` calls to revert when the rate is stale, preventing overminting. The threshold should be set to a value slightly above the expected keeper cadence. An owner-controlled setter for `maxStaleness` allows adjustment without redeployment.

## Proof of Concept

**Preconditions:**
- L1 `LRTOracle.rsETHPrice` = `1.05e18` (5% appreciation since last L2 update)
- `CrossChainRateReceiver.rate` = `1.00e18`, `lastUpdated` is 30+ hours old (keeper has not called `updateRate()`)

**Steps:**
1. Attacker observes `lastUpdated` on-chain; confirms rate is stale.
2. Attacker calls `RSETHPoolV3.deposit{value: 100 ether}("")`.
3. Pool calls `viewSwapRsETHAmountAndFee(100e18)` → `getRate()` returns `1.00e18`.
4. `rsETHAmount = 100e18 * 1e18 / 1.00e18 = 100e18` wrsETH minted.
5. Correct amount at current L1 rate: `100e18 * 1e18 / 1.05e18 ≈ 95.24e18` wrsETH.
6. Attacker receives ~4.76 excess wrsETH at no extra cost.
7. When the 100 ETH is bridged to L1 and deposited into KelpDAO, only ~95.24 rsETH is minted, leaving the pool ~4.76 rsETH short to cover all outstanding wrsETH. Existing holders bear the shortfall.

**Foundry fork test plan:**
- Fork an L2 (e.g. Arbitrum) at a block where `lastUpdated` is >24 hours old.
- Warp `block.timestamp` forward to simulate staleness.
- Call `deposit()` and assert `wrsETH.balanceOf(attacker) > expectedAmount` where `expectedAmount` uses the current L1 rate fetched via `LRTOracle`.
- Assert the pool's ETH-to-wrsETH backing ratio is below 1:1 at the current L1 rate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-105)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L303-307)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L422-426)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L281-285)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```
