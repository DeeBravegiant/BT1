### Title
Stale Rate Used in `getRate()` Without Staleness Check Enables Excess rsETH Minting — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` stores `lastUpdated` on every `lzReceive` call but `getRate()` returns `rate` unconditionally. If the LayerZero update pipeline stalls while the true L1 rsETH/ETH rate appreciates, any depositor can mint more rsETH than the current backing warrants, stealing accrued yield from existing holders.

---

### Finding Description

`CrossChainRateReceiver.getRate()` returns the stored `rate` with no check against `lastUpdated`: [1](#0-0) 

`lastUpdated` is written on every `lzReceive` call: [2](#0-1) 

but is **never read** inside `getRate()`. The contract has all the data needed for a staleness guard and deliberately omits it.

`RSETHPoolV3.getRate()` delegates directly to this oracle: [3](#0-2) 

`viewSwapRsETHAmountAndFee` uses the returned rate as the denominator: [4](#0-3) 

`deposit()` mints `rsETHAmount` directly from this calculation: [5](#0-4) 

---

### Impact Explanation

When `stale_rate < true_rate`:

```
rsETHAmount = amountAfterFee * 1e18 / stale_rate
            > amountAfterFee * 1e18 / true_rate   (correct amount)
```

The excess rsETH minted to the depositor is backed by no additional ETH. Because rsETH is a yield-bearing token whose value accrues to all holders proportionally, the over-issuance dilutes the share of yield belonging to existing holders — this is a direct theft of unclaimed yield. The daily mint limit caps the per-day damage but does not prevent the exploit within that window.

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

LayerZero pipeline stalls are a realistic operational scenario: unpaid relayer/oracle fees, LZ network congestion, or provider-side outages can all halt `lzReceive` calls for hours or days. No admin compromise, key leak, or governance capture is required. The attacker only needs to call the public `deposit()` function while the rate is stale. rsETH accrues yield continuously on L1, so even a multi-hour stall creates exploitable divergence.

---

### Recommendation

Add a configurable `maxStaleness` parameter and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This uses the already-stored `lastUpdated` variable and requires no structural changes.

---

### Proof of Concept

```solidity
// Fork test (local fork, no mainnet)
function testStaleRateYieldTheft() public {
    // 1. Record current rate (e.g. 1.05e18)
    uint256 initialRate = receiver.rate(); // stale_rate

    // 2. Simulate LZ pipeline stall: do NOT call lzReceive for N days
    vm.warp(block.timestamp + 7 days);

    // 3. True rate has appreciated to 1.08e18 on L1, but receiver.rate() still returns 1.05e18
    // (lzReceive never called)

    // 4. Attacker deposits 1 ETH
    uint256 depositAmount = 1 ether;
    vm.deal(attacker, depositAmount);
    vm.prank(attacker);
    pool.deposit{value: depositAmount}("ref");

    // 5. Assert attacker received more rsETH than current true rate warrants
    uint256 received = wrsETH.balanceOf(attacker);
    uint256 expectedAtTrueRate = depositAmount * 1e18 / 1.08e18;
    assertGt(received, expectedAtTrueRate, "Attacker minted excess rsETH");

    // 6. Compute yield stolen = (received - expectedAtTrueRate) * true_rate / 1e18
}
```

### Citations

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
