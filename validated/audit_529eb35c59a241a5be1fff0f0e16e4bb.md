### Title
Stale Cross-Chain Rate Trusted Without Staleness Validation Enables Over-Minting of rsETH at L2 Pools - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

### Summary
`CrossChainRateReceiver.getRate()` returns the last cached `rate` with no check against `lastUpdated`, meaning the L2 deposit pools (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPool`) will continue minting rsETH at a stale (outdated) exchange rate if the LayerZero cross-chain rate update is delayed. This is the direct DeFi analog of the biometric report: the system unconditionally trusts a cached external state (the cross-chain rate) without re-validating against the underlying source of truth (the L1 oracle), allowing any depositor to exploit the divergence window.

### Finding Description

The cross-chain rate pipeline works as follows:

1. On L1, `RSETHMultiChainRateProvider` (or `RSETHRateProvider`) reads `LRTOracle.rsETHPrice()` and sends it via LayerZero to the L2 `RSETHRateReceiver`, which is a concrete implementation of `CrossChainRateReceiver`.

2. `CrossChainRateReceiver.lzReceive()` stores the received value in `rate` and records `lastUpdated = block.timestamp`.

3. `CrossChainRateReceiver.getRate()` returns `rate` unconditionally — there is no maximum-staleness guard: [1](#0-0) 

4. Every L2 pool reads this oracle through `IOracle(rsETHOracle).getRate()`: [2](#0-1) 

5. The minting formula divides by the stale rate: [3](#0-2) 

The same pattern is present in `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee()`: [4](#0-3) 

And in `RSETHPool.viewSwapRsETHAmountAndFee()`: [5](#0-4) 

The `lastUpdated` field is stored but never read by any consumer: [6](#0-5) 

**Exploit window**: When the L1 rsETH price increases (e.g., from 1.05 ETH/rsETH to 1.10 ETH/rsETH due to accumulated staking rewards) and the LayerZero message is delayed — due to network congestion, a temporary LayerZero outage, or simply the normal latency between rate-update transactions — the L2 receiver continues to serve the old lower rate. Any depositor calling `deposit()` during this window receives `amount / 1.05e18` rsETH instead of the correct `amount / 1.10e18`, minting approximately 4.8% more rsETH than they are entitled to.

### Impact Explanation

**High — Theft of unclaimed yield.**

Each rsETH token represents a pro-rata claim on the underlying ETH held by the protocol. When new rsETH is minted at a stale lower rate, the total supply grows faster than the underlying ETH, diluting every existing holder's redemption value. The accrued staking yield that existing holders had earned is effectively transferred to the late depositor. The magnitude scales with: (a) the size of the deposit, (b) the percentage divergence between the stale and actual rate, and (c) the duration of the staleness window. Given that rsETH accrues yield continuously and rate updates are periodic, a multi-hour staleness window with a large deposit can represent a material theft of yield from all existing holders.

### Likelihood Explanation

**Medium.** LayerZero message delivery is not instantaneous and is subject to network congestion, relayer downtime, and gas price spikes on either chain. The rate provider must be called explicitly (it is not push-based on every L1 block), so the staleness window between two consecutive rate updates is a normal operating condition, not an edge case. A sophisticated depositor monitoring both the L1 oracle price and the L2 cached rate can identify and exploit the divergence window without any privileged access — only a standard `deposit()` call is required.

### Recommendation

Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(
        lastUpdated != 0 && block.timestamp - lastUpdated <= maxStaleness,
        "Rate is stale"
    );
    return rate;
}
```

This mirrors the fix recommended in the biometric report — requiring re-validation against the underlying credential (L1 price) before trusting the cached state. The `maxStaleness` value should be set conservatively (e.g., 24–48 hours) and be updatable by the owner. All L2 pool contracts inherit the protection automatically because they call `getRate()` through the `IOracle` interface.

### Proof of Concept

1. Observe that `LRTOracle.rsETHPrice` on L1 is `1.10e18` (rsETH has appreciated from `1.05e18`).
2. Observe that `CrossChainRateReceiver.rate` on the target L2 is still `1.05e18` (the LayerZero update has not yet been relayed — `lastUpdated` is 12 hours old).
3. Call `RSETHPoolV3.deposit{value: 100 ether}("")` on the L2 pool.
4. The pool computes `rsETHAmount = 100e18 * 1e18 / 1.05e18 ≈ 95.24 rsETH` instead of the correct `100e18 * 1e18 / 1.10e18 ≈ 90.91 rsETH`.
5. The attacker receives `≈ 4.33 rsETH` more than entitled, at the expense of all existing rsETH holders whose redemption value is diluted by the inflated supply.
6. No privileged access is required; the only precondition is that the cross-chain rate has not been updated recently, which is a normal operating condition.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-17)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L234-237)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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
