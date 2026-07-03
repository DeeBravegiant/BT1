### Title
Stale Cached Rate Used in L2 Pool rsETH Minting Calculations Due to No Staleness Check in `CrossChainRateReceiver.getRate()` - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary
`CrossChainRateReceiver.getRate()` returns a cached `rate` state variable without ever validating the `lastUpdated` timestamp. All L2 pool contracts (`RSETHPool`, `RSETHPoolV3`, and their variants) call `IOracle(rsETHOracle).getRate()` to determine how many rsETH/wrsETH tokens to mint per unit of ETH or LST deposited. If the cross-chain rate has not been refreshed recently, the stale rate is silently used, causing users to receive an incorrect number of tokens.

---

### Finding Description

`CrossChainRateReceiver` stores two state variables: `rate` (the last received rsETH/ETH exchange rate) and `lastUpdated` (the timestamp of the last LayerZero message). [1](#0-0) 

The `lzReceive` function updates both when a message arrives: [2](#0-1) 

However, `getRate()` returns `rate` unconditionally, with no check against `lastUpdated`: [3](#0-2) 

Every L2 pool contract calls this function to price deposits. In `RSETHPoolV3`: [4](#0-3) 

This rate is then used directly in `viewSwapRsETHAmountAndFee` to compute how many wrsETH tokens to mint: [5](#0-4) 

The same pattern exists in `RSETHPool.viewSwapRsETHAmountAndFee`: [6](#0-5) 

The analog to the Balancer finding is direct: just as `getLastInvariant()` returns a cached amplification factor that may no longer reflect the pool's current state, `CrossChainRateReceiver.getRate()` returns a cached exchange rate that may no longer reflect the current L1 rsETH price — and in both cases the code uses the stale cached value without checking whether a fresher value is available.

---

### Impact Explanation

rsETH price monotonically increases over time as staking rewards accrue. If the cached `rate` is lower than the true current rate (i.e., the rate has not been updated for some time), then:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
```

A lower (stale) `rsETHToETHrate` produces a **larger** `rsETHAmount`. Users depositing during a stale-rate window receive more wrsETH than the current exchange rate entitles them to. This over-issuance dilutes all existing rsETH/wrsETH holders, effectively transferring unclaimed yield from existing holders to new depositors.

The impact maps to **High — Theft of unclaimed yield** from existing rsETH holders.

---

### Likelihood Explanation

The rate is propagated from L1 to L2 via LayerZero only when `MultiChainRateProvider.updateRate()` is called by an off-chain keeper. There is no on-chain enforcement of update frequency. A keeper outage, a LayerZero message delivery delay, or simply infrequent calls to `updateRate()` are all realistic scenarios that leave `rate` stale for hours or days. The `lastUpdated` field is already stored, confirming the developers anticipated the need to track freshness, but the check was never implemented.

---

### Recommendation

Add a configurable `maxStaleness` parameter and enforce it inside `getRate()`:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This mirrors the fix recommended in the Balancer report: use the most current available value rather than a potentially outdated cached one.

---

### Proof of Concept

1. At time `T`, `lzReceive` delivers `rate = 1.05e18` (rsETH/ETH) and sets `lastUpdated = T`.
2. Staking rewards accrue; the true L1 rsETH price rises to `1.07e18` by time `T + 2 days`.
3. The keeper does not call `updateRate()` during this window (outage or oversight).
4. A user calls `RSETHPoolV3.deposit{value: 1 ether}()` at `T + 2 days`.
5. `getRate()` returns the stale `1.05e18` instead of `1.07e18`.
6. `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.952 wrsETH` (correct would be `≈ 0.935 wrsETH`).
7. The user receives ~1.8% more wrsETH than entitled, at the expense of all existing holders. [3](#0-2) [5](#0-4)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L12-16)
```text
    /// @notice Last rate updated on the receiver
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-99)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
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
