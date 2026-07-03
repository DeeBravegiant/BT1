### Title
Missing Staleness Check in `CrossChainRateReceiver.getRate()` Enables Mispriced wrsETH Minting — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the last stored `rate` with no check against `lastUpdated`. All three L2 deposit pools (`RSETHPoolV3`, `RSETHPoolV2`, `RSETHPoolNoWrapper`) consume this value directly to compute how many wrsETH tokens to mint. When the LayerZero relay is delayed or stalled, the stored rate diverges from the true L1 rsETH price, causing every deposit and redemption to be priced at the wrong rate.

---

### Finding Description

`CrossChainRateReceiver` stores two state variables set only inside `lzReceive`:

```solidity
rate = _rate;
lastUpdated = block.timestamp;
``` [1](#0-0) 

The public read function exposes the raw stored value with no age validation:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
``` [2](#0-1) 

Every L2 pool delegates its pricing to this function through the same `IOracle` interface:

```solidity
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
``` [3](#0-2) 

The minting formula in all three pools divides by this rate:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [4](#0-3) [5](#0-4) [6](#0-5) 

There is no guard anywhere in the deposit path that checks `block.timestamp - lastUpdated < maxStaleness`.

---

### Impact Explanation

The direction of harm depends on which way the rate has drifted:

**Scenario A — stale rate < true rate (e.g., stored 1e18, true 1.05e18):**
A depositor sends 1 ETH and receives `1e18 * 1e18 / 1e18 = 1 wrsETH`. At the true rate they should receive only `1e18 * 1e18 / 1.05e18 ≈ 0.952 wrsETH`. The extra 0.048 wrsETH is minted from the yield that existing holders had already accrued — their unclaimed yield is permanently diluted/stolen. This maps to **High: Theft of unclaimed yield**.

**Scenario B — stale rate > true rate (e.g., stored 1.05e18, true 1e18):**
A depositor sends 1 ETH and receives only `≈ 0.952 wrsETH` when they should receive `1 wrsETH`. The 0.048 wrsETH difference is permanently unclaimable by that depositor. This maps to **Medium: Permanent freezing of unclaimed yield**.

> **Note on the question's proof idea:** The scenario described (stale 1e18, true 1.05e18) actually produces Scenario A — the depositor *benefits* and existing holders are harmed. The "permanent freezing of unclaimed yield *for the depositor*" framing only holds under Scenario B. Both scenarios are reachable in production; the vulnerability is valid for both impact levels.

---

### Likelihood Explanation

`updateRate()` on the provider is permissionless and callable by anyone willing to pay the LayerZero fee, but it is not automated on-chain. Any period of bridge congestion, fee spike, or operator inattention leaves the L2 rate stale. The longer the gap, the larger the mispricing. This is a realistic operational condition, not a theoretical one.

---

### Recommendation

Add a configurable `maxStaleness` parameter and revert in `getRate()` if the stored rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
``` [2](#0-1) 

This causes deposits to revert rather than execute at a stale price, giving operators time to refresh the rate before funds are mispriced.

---

### Proof of Concept

```solidity
// Fork test (Scenario B: stale rate > true rate)
function test_staleRateFreezeYield() external {
    // Simulate: lzReceive was called 3 days ago with rate = 1.05e18
    // True current rsETH price has since dropped to 1.00e18
    vm.store(address(rateReceiver), bytes32(uint256(0)), bytes32(uint256(1.05e18))); // rate slot
    vm.store(address(rateReceiver), bytes32(uint256(1)), bytes32(block.timestamp - 3 days)); // lastUpdated slot

    uint256 depositAmount = 1 ether;
    vm.deal(user, depositAmount);

    vm.prank(user);
    pool.deposit{value: depositAmount}("ref");

    uint256 wrsETHReceived = wrsETH.balanceOf(user);
    // At stale 1.05e18: user gets ~0.952e18 wrsETH
    // At true 1.00e18: user should get 1.000e18 wrsETH
    // Delta of ~0.048e18 wrsETH is permanently unclaimable
    assertLt(wrsETHReceived, 1 ether, "User received less wrsETH than entitled");
}
```

The root cause is confirmed at: [2](#0-1)

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

**File:** contracts/pools/RSETHPoolV3.sol (L307-307)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV2.sol (L233-233)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L285-285)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
