### Title
Missing Staleness Check in `CrossChainRateReceiver.getRate()` Allows Stale Rate to Drive rsETH Minting — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` stores a `lastUpdated` timestamp but never uses it to validate freshness. `getRate()` unconditionally returns the stored `rate`, which can be arbitrarily old. All L2 pool contracts (`RSETHPoolV2`, `RSETHPoolV3`, etc.) delegate their rate lookup to this function with no additional staleness guard, meaning deposits can mint rsETH at a rate that is days or weeks out of date.

---

### Finding Description

`CrossChainRateReceiver.lzReceive()` is the sole update path for the stored rate. It sets both `rate` and `lastUpdated` when a LayerZero message arrives: [1](#0-0) 

`getRate()` returns `rate` with no reference to `lastUpdated`: [2](#0-1) 

A grep across the entire repo confirms `lastUpdated` is **written** in `CrossChainRateReceiver.sol` and `CrossChainRateProvider.sol` but **never read** for any validation. It is purely informational.

`RSETHPoolV2.getRate()` and `RSETHPoolV3.getRate()` both delegate directly to `IOracle(rsETHOracle).getRate()` with no additional freshness check: [3](#0-2) [4](#0-3) 

The deposit flow is:

```
RSETHPoolV2.deposit()
  → viewSwapRsETHAmountAndFee(amount)
      → getRate()                          // RSETHPoolV2
          → IOracle(rsETHOracle).getRate() // CrossChainRateReceiver
              → return rate;               // no staleness check
  → rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
  → wrsETH.mint(msg.sender, rsETHAmount)
``` [5](#0-4) [6](#0-5) 

---

### Impact Explanation

rsETH is a liquid staking token whose ETH exchange rate monotonically increases as staking rewards accrue. A stale (lower) rate causes the formula `amountAfterFee * 1e18 / rsETHToETHrate` to mint **more** rsETH per ETH than the current backing warrants, diluting existing holders. Conversely, if the rate were somehow higher than current (e.g., after a slashing event), depositors would receive fewer rsETH than owed. In either case the contract fails to deliver the promised exchange rate. No direct ETH is lost from the pool itself, placing this in the **Low** scope: contract fails to deliver promised returns, but doesn't lose value.

---

### Likelihood Explanation

LayerZero message delivery is not guaranteed. Network congestion, dropped messages, or the off-chain keeper failing to call `updateRate` on the provider are all realistic operational conditions. The longer the gap, the larger the rate deviation. This is not a theoretical edge case — it is an expected operational scenario for any cross-chain oracle.

---

### Recommendation

Add a configurable `maxStaleness` threshold and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(lastUpdated != 0, "Rate not initialized");
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
``` [2](#0-1) 

---

### Proof of Concept

```solidity
// Invariant fuzz test (Foundry)
function testStaleRateMintsBadAmount(uint256 elapsed) public {
    elapsed = bound(elapsed, 7 days, 365 days);

    // Deploy RSETHRateReceiver with initial rate = 1.05e18
    // lzReceive is NOT called again
    vm.warp(block.timestamp + elapsed);

    // Rate is still 1.05e18 even though real rate may be 1.10e18
    uint256 rate = receiver.getRate();
    assertEq(rate, 1.05e18); // stale rate returned, no revert

    // Pool mints at stale rate
    (uint256 rsETHAmount,) = pool.viewSwapRsETHAmountAndFee(1 ether);
    // rsETHAmount is computed from 1.05e18 instead of current 1.10e18
    // depositor receives ~4.5% more rsETH than warranted
}
``` [7](#0-6) [1](#0-0)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L16-16)
```text
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L200-203)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-218)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
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

**File:** contracts/pools/RSETHPoolV3.sol (L234-237)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```
