### Title
Stale Cached Rate Returned Without Staleness Check Enables Over-Minting of wrsETH - (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` locally caches the rsETH/ETH exchange rate received via LayerZero and exposes it through `getRate()`. This function returns the cached `rate` with no check against `lastUpdated`, meaning any consumer — including `RSETHPoolV3` — will silently use a stale rate. When rsETH has appreciated on L1 but the L2 cache has not been refreshed, any depositor can call `deposit()` and receive more `wrsETH` than the current rsETH value warrants, constituting theft of yield from existing rsETH holders.

---

### Finding Description

`CrossChainRateReceiver` stores the rsETH/ETH rate in `rate` and records the update time in `lastUpdated`: [1](#0-0) 

The `lzReceive` function updates both fields when a LayerZero message arrives from the L1 rate provider: [2](#0-1) 

However, the public `getRate()` function returns the cached `rate` unconditionally — `lastUpdated` is stored but **never consulted** when the rate is read: [3](#0-2) 

`RSETHPoolV3` sets `rsETHOracle` to a `RSETHRateReceiver` (the concrete subclass of `CrossChainRateReceiver`) and calls `getRate()` inside `viewSwapRsETHAmountAndFee` to compute how much `wrsETH` to mint: [4](#0-3) 

The minting formula is `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`. A stale-low rate (rsETH has appreciated on L1 but the L2 cache is old) produces a larger `rsETHAmount` than the depositor is entitled to.

The `updateRate()` function on the L1 `MultiChainRateProvider` must be called manually and paid for with ETH for LayerZero gas: [5](#0-4) 

There is no on-chain enforcement that this is called within any time bound, and no staleness guard on the consumer side.

---

### Impact Explanation

When the cached rate is stale-low (rsETH has appreciated on L1 but the L2 receiver has not been updated), every depositor calling `deposit()` on `RSETHPoolV3` receives more `wrsETH` than the current rsETH value warrants. The excess `wrsETH` represents value diluted from existing rsETH/wrsETH holders. This is **theft of unclaimed yield** (High impact).

---

### Likelihood Explanation

- `updateRate()` requires a manual call and ETH payment for LayerZero fees; it is not automatic.
- LayerZero message delivery can be delayed during network congestion.
- During periods of rapid rsETH appreciation (e.g., after a large reward distribution updates `rsETHPrice` on L1), the L2 rate will lag.
- Any unprivileged depositor can observe the stale rate on-chain and exploit it immediately by calling `deposit()`.

---

### Recommendation

Add a configurable `maxStaleness` parameter and revert in `getRate()` if the cached rate is too old:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This mirrors the fix applied to the Redstone adapter: ensure that stale cached data is never silently consumed.

---

### Proof of Concept

1. L1 `LRTOracle.rsETHPrice` is updated to `1.05e18` (rsETH appreciated).
2. `updateRate()` on the L1 provider has not been called; the L2 `CrossChainRateReceiver.rate` still holds `1.00e18`.
3. Attacker calls `RSETHPoolV3.deposit{value: 1 ether}("")`.
4. Pool calls `IOracle(rsETHOracle).getRate()` → returns `1.00e18` (stale).
5. `rsETHAmount = 1e18 * 1e18 / 1.00e18 = 1.000e18` wrsETH minted.
6. Correct amount at current rate: `1e18 * 1e18 / 1.05e18 ≈ 0.952e18` wrsETH.
7. Attacker receives ~5% excess wrsETH, diluting existing holders. [3](#0-2) [6](#0-5)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
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

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```
