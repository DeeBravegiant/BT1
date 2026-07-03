Audit Report

## Title
Stale Cross-Chain Rate Consumed Without Freshness Validation Enables Excess rsETH Minting on L2 Pools - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

## Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally, never consulting the `lastUpdated` timestamp. All three L2 deposit pools (`RSETHPool`, `RSETHPoolV3`, `RSETHPoolNoWrapper`) use this rate as the denominator in their rsETH minting formula. When the LayerZero rate-update pipeline stalls, the stored rate becomes arbitrarily stale, causing depositors to receive more rsETH than the protocol's current backing justifies, diluting accrued yield of all existing rsETH holders.

## Finding Description

`CrossChainRateReceiver.lzReceive()` records the received rate and a timestamp:

```solidity
rate = _rate;
lastUpdated = block.timestamp;
```

`getRate()` returns `rate` with no staleness guard:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
```

`lastUpdated` is stored but **never read** anywhere in the contract. Every L2 pool's deposit path calls `IOracle(rsETHOracle).getRate()`, which resolves to this function:

- `RSETHPool.viewSwapRsETHAmountAndFee` → `getRate()` → `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`
- `RSETHPoolV3.viewSwapRsETHAmountAndFee` → same pattern
- `RSETHPoolNoWrapper.viewSwapRsETHAmountAndFee` → same pattern

Because rsETH is a yield-bearing token, its ETH price monotonically increases over time. A stale (old, lower) rate causes the denominator to be smaller than the true current rate, so depositors receive more rsETH than the protocol's actual backing justifies. No existing check — `whenNotPaused`, `limitDailyMint`, `nonReentrant` — validates the age of the oracle rate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

## Impact Explanation

**High — Theft of unclaimed yield.**

Every deposit made while the rate is stale mints excess rsETH. The excess represents a claim on protocol assets that was not earned by the depositor. This dilutes the share value of all existing rsETH holders, effectively transferring their accrued staking yield to the depositor. The magnitude scales with: (a) how stale the rate is, (b) the deposit volume during the stale window, and (c) the rate of rsETH price appreciation. This matches the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation

**Medium.** LayerZero message delivery is not guaranteed to be continuous. Network congestion, relayer downtime, or a gap in the rate-push cadence from `RSETHRateProvider` / `RSETHMultiChainRateProvider` can all cause the stored rate to lag behind the true L1 price. The contract provides no on-chain circuit-breaker: there is no maximum acceptable age for `lastUpdated`, and no revert or fallback when the rate is stale. The condition is reachable by any depositor who simply calls `deposit()` during a stale window — no special privilege required.

## Recommendation

Add a staleness guard inside `getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

Long term, implement a validated rate-consumption framework analogous to Chainlink's `latestRoundData` pattern: always check both the returned value (non-zero, within plausible bounds) and the timestamp before using any externally sourced rate in a minting or pricing calculation.

## Proof of Concept

1. At time T, `CrossChainRateReceiver.rate` = 1.05e18 (rsETH/ETH). `lastUpdated` = T.
2. LayerZero relayer goes offline; no further `lzReceive` calls occur.
3. At time T + 7 days, the true rsETH price on L1 is 1.06e18 (staking rewards accrued).
4. A depositor calls `RSETHPoolV3.deposit{value: 100 ether}("")`.
5. `viewSwapRsETHAmountAndFee(100e18)` calls `getRate()` → returns 1.05e18 (stale).
6. `rsETHAmount = 100e18 * 1e18 / 1.05e18 ≈ 95.238 rsETH`.
7. Correct amount at current rate: `100e18 * 1e18 / 1.06e18 ≈ 94.340 rsETH`.
8. Depositor receives ~0.898 excess rsETH (~0.95% over-issuance), extracted from existing holders' accrued yield.
9. `getRate()` never reverted; `lastUpdated` was never checked.

**Foundry fork test plan:** Fork the target L2, deploy a mock `CrossChainRateReceiver` with `rate = 1.05e18` and `lastUpdated = block.timestamp - 7 days`. Point `RSETHPoolV3.rsETHOracle` to it. Call `deposit{value: 100 ether}("")` and assert that `rsETHAmount` exceeds `100e18 * 1e18 / 1.06e18`, confirming over-issuance with no revert.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-105)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPool.sol (L311-320)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```
