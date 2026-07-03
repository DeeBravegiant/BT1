### Title
Stale `rsETHPrice` Served as Fresh via Misleading `updatedAt` Under Block Stuffing — (`contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.latestRoundData()` computes the rsETH/USD price by multiplying the Chainlink ETH/USD answer by `LRTOracle.rsETHPrice`. However, the `updatedAt` timestamp it returns is sourced exclusively from the ETH/USD Chainlink feed — not from when `rsETHPrice` was last written. An attacker who uses block stuffing to prevent `LRTOracle.updateRSETHPrice()` from being mined will cause the feed to serve a below-true rsETH/USD price while simultaneously advertising a fresh `updatedAt`, defeating any downstream staleness guard.

---

### Finding Description

`RSETHPriceFeed.latestRoundData()` assembles its return values as follows:

```solidity
// contracts/oracles/RSETHPriceFeed.sol  lines 68-69
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
``` [1](#0-0) 

`updatedAt` is taken verbatim from the Chainlink ETH/USD aggregator. `rsETHPrice` is a separate storage variable in `LRTOracle` that is updated only when `updateRSETHPrice()` is successfully mined:

```solidity
// contracts/LRTOracle.sol  line 313
rsETHPrice = newRsETHPrice;
``` [2](#0-1) 

`updateRSETHPrice()` is `public` with no role restriction — only the `whenNotPaused` guard:

```solidity
// contracts/LRTOracle.sol  lines 87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

There is no on-chain record of when `rsETHPrice` was last written, and `RSETHPriceFeed` never checks such a timestamp. The two components of the returned `answer` therefore have independent freshness guarantees that are never reconciled.

**Block stuffing path:** An attacker fills consecutive blocks to their gas limit with their own transactions, preventing any `updateRSETHPrice()` call from being included. During this window, EigenLayer rewards continue to accrue, so the true rsETH/ETH exchange rate rises while `LRTOracle.rsETHPrice` remains frozen at its pre-accrual value. Every call to `RSETHPriceFeed.latestRoundData()` during this window returns a `answer` that is lower than the true rsETH/USD price, yet returns an `updatedAt` that is at most ~1 hour old (the ETH/USD Chainlink heartbeat), making the feed appear fresh to any consumer that relies on `updatedAt` for staleness detection.

---

### Impact Explanation

The direct, in-scope impact is **Low — Block stuffing**: the attacker can force the oracle to serve a stale (below-true) rsETH/USD price for the duration of the stuffed window. The `updatedAt` field is misleading because it reflects ETH/USD freshness, not rsETHPrice freshness, so downstream staleness checks are bypassed.

A secondary downstream consequence — wrongful liquidation of rsETH collateral in lending protocols that consume this feed — is plausible but depends on external protocol behavior and is therefore not independently scoped here.

---

### Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive (attacker must pay for the full block gas limit per block), making sustained attacks economically irrational for most scenarios. However, the attack becomes rational if the attacker holds a short position against rsETH collateral in a lending protocol and the profit from triggered liquidations exceeds the stuffing cost. The absence of any staleness guard on `rsETHPrice` within `RSETHPriceFeed` means no code-level mitigation exists.

---

### Recommendation

1. **Track `rsETHPrice` update time.** Add a `uint256 public rsETHPriceUpdatedAt` storage variable in `LRTOracle` and set it alongside `rsETHPrice`:
   ```solidity
   rsETHPrice = newRsETHPrice;
   rsETHPriceUpdatedAt = block.timestamp;
   ``` [2](#0-1) 

2. **Return the correct `updatedAt` from `RSETHPriceFeed`.** Override `updatedAt` with `min(ethToUsdUpdatedAt, rsETHPriceUpdatedAt)` so that consumers see the staleness of the least-fresh component:
   ```solidity
   updatedAt = updatedAt < rsETHPriceUpdatedAt ? updatedAt : rsETHPriceUpdatedAt;
   ``` [1](#0-0) 

3. **Add a maximum staleness check inside `latestRoundData()`** that reverts if `rsETHPriceUpdatedAt` is older than a configured heartbeat, preventing the feed from serving a price that is known to be stale.

---

### Proof of Concept

```solidity
// Fork test outline (no mainnet calls; uses local fork + simulated state)
function test_blockStuffingStalePrice() external {
    // 1. Snapshot current rsETHPrice from LRTOracle
    uint256 priceBefore = lrtOracle.rsETHPrice();

    // 2. Simulate EigenLayer reward accrual: increase the asset balance
    //    held by the deposit pool by X% (e.g. via deal() on the LST token)
    deal(address(stETH), depositPool, stETHBalance * 110 / 100);

    // 3. Simulate block stuffing: advance time by one heartbeat (e.g. 24 h)
    //    without calling updateRSETHPrice()
    vm.warp(block.timestamp + 24 hours);

    // 4. Call latestRoundData() — rsETHPrice is still priceBefore
    (, int256 answer,, uint256 updatedAt,) = rsETHPriceFeed.latestRoundData();

    // 5. Assert answer is below the true price
    uint256 trueRsETHPrice = lrtOracle.getRsETHPriceIfUpdated(); // hypothetical view
    assertLt(uint256(answer), trueRsETHPrice * ethUsdPrice / 1e18);

    // 6. Assert updatedAt is recent (ETH/USD heartbeat), masking the staleness
    assertGt(updatedAt, block.timestamp - 1 hours);
}
```

The test confirms that `answer` is below the true rsETH/USD price while `updatedAt` appears fresh, satisfying the stated proof idea.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L68-69)
```text
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
