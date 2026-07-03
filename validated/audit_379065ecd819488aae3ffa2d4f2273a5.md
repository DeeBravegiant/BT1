### Title
Missing Staleness Check on Cross-Chain Rate Allows Silent Over-Minting of wrsETH — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` records `lastUpdated` when a LayerZero message arrives but **never enforces a freshness window** when `getRate()` is called. `RSETHPoolV3.deposit()` consumes this rate unconditionally. If the LayerZero relayer stops delivering messages (e.g., gas funding lapses), the stale, lower rate persists indefinitely and causes every subsequent deposit to mint more `wrsETH` than the depositor is entitled to, diluting existing holders' accrued yield.

---

### Finding Description

`CrossChainRateReceiver` stores two state variables:

```solidity
uint256 public rate;        // last received rsETH/ETH rate
uint256 public lastUpdated; // timestamp of last lzReceive call
``` [1](#0-0) 

`lastUpdated` is written inside `lzReceive`:

```solidity
rate = _rate;
lastUpdated = block.timestamp;
``` [2](#0-1) 

But `getRate()` returns `rate` with **no staleness check**:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
``` [3](#0-2) 

`RSETHPoolV3.getRate()` delegates directly to this oracle:

```solidity
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
``` [4](#0-3) 

`viewSwapRsETHAmountAndFee` uses this rate to compute the mint amount:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [5](#0-4) 

`deposit()` calls `viewSwapRsETHAmountAndFee` and mints the result:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
wrsETH.mint(msg.sender, rsETHAmount);
``` [6](#0-5) 

There is no point in the entire call chain — `deposit` → `viewSwapRsETHAmountAndFee` → `getRate` → `CrossChainRateReceiver.getRate` — where `lastUpdated` is read or compared against any maximum staleness threshold. The variable is stored but never consumed in any guard.

---

### Impact Explanation

rsETH is a yield-bearing token whose ETH price increases monotonically as staking rewards accrue. A stale rate is therefore always **lower** than the true current rate. Because the mint formula is:

```
rsETHAmount = amountAfterFee * 1e18 / staleRate
```

a lower `staleRate` produces a **larger** `rsETHAmount`. Every depositor during the stale window receives more `wrsETH` than the ETH they contributed warrants. This inflates total `wrsETH` supply without a corresponding increase in backing rsETH, permanently diluting the yield that existing holders have already accrued but not yet claimed — **theft of unclaimed yield (High)**.

---

### Likelihood Explanation

LayerZero relayer liveness depends on continuous gas funding by the operator. A funding lapse, relayer misconfiguration, or sustained network congestion on the destination chain can halt message delivery for hours or days without any on-chain signal. No admin action is required to trigger the condition; it arises from ordinary operational failure. The contract provides no circuit-breaker that would pause deposits when the rate is stale.

---

### Recommendation

Add a configurable `maxStaleness` parameter and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
``` [3](#0-2) 

This ensures that if the LayerZero relayer stops, deposits revert rather than proceeding with a mispriced rate.

---

### Proof of Concept

```solidity
// Fork test (Arbitrum or any L2 where RSETHPoolV3 is deployed)
// 1. Deploy / fork RSETHPoolV3 pointing at RSETHRateReceiver as rsETHOracle.
// 2. Record current rate R0 = receiver.rate() and lastUpdated T0.
// 3. vm.warp(block.timestamp + 30 days);  // advance 30 days, no lzReceive called
// 4. Assert receiver.lastUpdated() == T0  (rate is stale)
// 5. Assert receiver.getRate() == R0      (no revert, stale rate returned)
// 6. uint256 trueRate = LRTOracle.rsETHPrice(); // fetch current on-chain price
// 7. Assert trueRate > R0                 (price has grown over 30 days)
// 8. uint256 depositAmt = 1 ether;
//    uint256 mintedActual   = depositAmt * 1e18 / R0;       // what pool mints
//    uint256 mintedExpected = depositAmt * 1e18 / trueRate; // what it should mint
//    Assert mintedActual > mintedExpected  // over-minting confirmed
// 9. Call pool.deposit{value: depositAmt}("") and verify wrsETH balance == mintedActual.
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-262)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L304-307)
```text
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
