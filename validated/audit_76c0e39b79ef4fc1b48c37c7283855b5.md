### Title
Composite rsETH/USD Price Carries Misleading `updatedAt` Timestamp, Masking Stale rsETH Component — (File: `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.latestRoundData()` and `getRoundData()` return `updatedAt` taken verbatim from the Chainlink ETH/USD feed, while the rsETH component of the composite price (`RS_ETH_ORACLE.rsETHPrice()`) is a separately-cached storage variable in `LRTOracle` with no on-chain timestamp. When `LRTOracle.rsETHPrice` is stale but the ETH/USD feed has been updated recently, any downstream consumer that uses `updatedAt` as a freshness guard will treat an outdated rsETH/USD price as live.

---

### Finding Description

In `RSETHPriceFeed.latestRoundData()`:

```solidity
// contracts/oracles/RSETHPriceFeed.sol  lines 68-69
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`updatedAt` is assigned entirely from the ETH/USD Chainlink round. [1](#0-0) 

`RS_ETH_ORACLE.rsETHPrice()` resolves to `LRTOracle.rsETHPrice`, a plain `uint256` storage slot: [2](#0-1) 

That slot is only written inside `_updateRsETHPrice()`, which is called by the public `updateRSETHPrice()` or the manager-gated `updateRSETHPriceAsManager()`: [3](#0-2) 

The final write is at the very end of `_updateRsETHPrice()`: [4](#0-3) 

**Critically, `LRTOracle` stores no timestamp alongside `rsETHPrice`.** There is no `rsETHPriceUpdatedAt` field, no `block.timestamp` snapshot, and no mechanism for `RSETHPriceFeed` to know how old the cached value is. The same flaw exists in `getRoundData()`: [5](#0-4) 

**Staleness scenario:**
1. `updateRSETHPrice()` is called at T=0; `rsETHPrice` is set.
2. Time advances N hours; `updateRSETHPrice()` is not called.
3. Chainlink's ETH/USD heartbeat fires at T+N; `updatedAt` is now T+N.
4. `RSETHPriceFeed.latestRoundData()` returns `updatedAt = T+N` (fresh) but `answer` still embeds the T=0 rsETH rate.
5. A downstream consumer with a staleness check like `require(block.timestamp - updatedAt < threshold)` passes the check and consumes the stale composite price.

---

### Impact Explanation

Any protocol that integrates `RSETHPriceFeed` as a Chainlink-compatible oracle and relies on `updatedAt` for freshness validation — a standard pattern in lending protocols (Aave, Compound forks), yield distributors, and collateral pricers — will silently accept a stale rsETH/USD price. Depending on the direction of drift:

- **Inflated rsETH price** (rewards accrued but not yet reflected in a price decrease): over-valued collateral → under-collateralized borrowing; over-stated yield → excess yield claims.
- **Deflated rsETH price** (slashing event not yet reflected): under-valued collateral → forced liquidations at wrong price; under-stated yield → yield withheld from legitimate claimants.

Both directions map to **theft of unclaimed yield** (High) and potentially collateral mispricing.

---

### Likelihood Explanation

- `updateRSETHPrice()` is not called on-chain automatically; it depends on off-chain keepers or manual calls. Any keeper downtime, gas spike, or deliberate delay creates a staleness window.
- The ETH/USD Chainlink feed has a 1-hour heartbeat on mainnet, so `updatedAt` will appear fresh even after many hours of rsETH oracle silence.
- No special role or privilege is required to trigger the observable condition — an unprivileged observer simply waits and then calls `latestRoundData()`.

---

### Recommendation

Track the timestamp of the last `rsETHPrice` update in `LRTOracle`:

```solidity
uint256 public rsETHPriceUpdatedAt;
// inside _updateRsETHPrice(), after rsETHPrice = newRsETHPrice:
rsETHPriceUpdatedAt = block.timestamp;
```

Then in `RSETHPriceFeed.latestRoundData()` / `getRoundData()`, return the **minimum** of the two timestamps:

```solidity
uint256 rsETHUpdatedAt = RS_ETH_ORACLE.rsETHPriceUpdatedAt();
updatedAt = updatedAt < rsETHUpdatedAt ? updatedAt : rsETHUpdatedAt;
```

This ensures `updatedAt` reflects the staleness of the least-recently-updated component of the composite price.

---

### Proof of Concept

```solidity
// Foundry fork test (no mainnet calls; mock or local deployment)
function test_staleRsETHMaskedByFreshUpdatedAt() public {
    // 1. Deploy LRTOracle + RSETHPriceFeed with a mock ETH/USD feed
    MockChainlinkFeed ethUsd = new MockChainlinkFeed(2000e8, block.timestamp);
    LRTOracle oracle = /* deploy */;
    RSETHPriceFeed feed = new RSETHPriceFeed(address(ethUsd), address(oracle), "rsETH/USD");

    // 2. Set initial rsETHPrice (e.g. 1.05 ETH)
    oracle.updateRSETHPrice(); // rsETHPrice = 1.05e18

    // 3. Advance time 2 hours; update ETH/USD feed but NOT rsETHPrice
    vm.warp(block.timestamp + 2 hours);
    ethUsd.setUpdatedAt(block.timestamp); // ETH/USD heartbeat fires

    // 4. Call latestRoundData
    (, int256 answer, , uint256 updatedAt,) = feed.latestRoundData();

    // 5. updatedAt is NOW (fresh), but rsETHPrice is 2 hours old
    assertEq(updatedAt, block.timestamp);          // passes — appears fresh
    // answer still uses the 2-hour-old rsETHPrice
    // A downstream consumer with `require(block.timestamp - updatedAt < 3600)` accepts this

    // 6. If rsETH actually appreciated 5% in those 2 hours (not yet reflected),
    //    a yield-claim contract using this feed under-pays claimants by 5%.
    //    Conversely, if rsETH dropped, it over-pays.
}
```

The test requires no admin compromise, no front-running, and no external protocol failure — only the normal condition of `updateRSETHPrice()` not being called for a period shorter than the downstream consumer's staleness threshold.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L53-61)
```text
    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L68-69)
```text
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
