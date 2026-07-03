### Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` for a Composite Answer That Includes a Separately-Staleable rsETH/ETH Component — (`contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.latestRoundData()` computes `answer` as `rsETHPrice × ETH/USD / 1e18`, but the `updatedAt` it returns is sourced exclusively from the ETH/USD Chainlink feed. The rsETH/ETH component (`LRTOracle.rsETHPrice`) has its own independent update cadence driven by the public `updateRSETHPrice()` call, and no timestamp for that component is ever surfaced. Any consumer performing a standard Chainlink staleness check on `updatedAt` will only verify ETH/USD freshness, not rsETH/ETH freshness, making the composite price undetectably stale in the rsETH dimension.

---

### Finding Description

`RSETHPriceFeed.latestRoundData()` is:

```solidity
// contracts/oracles/RSETHPriceFeed.sol lines 63-70
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

`updatedAt` is the timestamp from the ETH/USD Chainlink aggregator — it is refreshed every Chainlink heartbeat (typically 1 hour on mainnet, shorter on L2s). `RS_ETH_ORACLE.rsETHPrice()` is the stored value in `LRTOracle`, which is only updated when the public `updateRSETHPrice()` is called:

```solidity
// contracts/LRTOracle.sol line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

`LRTOracle` stores no `lastUpdatedAt` timestamp for `rsETHPrice`. The `RSETHPriceFeed` never reads or exposes one. The returned `updatedAt` is therefore structurally incapable of reflecting rsETH/ETH staleness.

**Block-stuffing attack path (on Morph L2 where the contract is deployed):**

1. Attacker fills blocks on Morph to prevent any `updateRSETHPrice()` transaction from landing. On an L2 with low block gas limits, this is significantly cheaper than on Ethereum mainnet.
2. During the stuffing window, the Chainlink ETH/USD feed continues to update normally (it is pushed by Chainlink's own infrastructure and cannot be blocked by stuffing user-space transactions).
3. `latestRoundData()` continues to return a fresh `updatedAt` (from ETH/USD), while `rsETHPrice` is frozen at its last stored value.
4. If ETH/USD moves materially during this window, the composite `answer` diverges from the true rsETH/USD price in the ETH/USD direction while the rsETH/ETH component is stale.
5. No single-timestamp staleness check (`block.timestamp - updatedAt > threshold`) can detect this, because `updatedAt` tracks only the ETH/USD leg.

---

### Impact Explanation

`RSETHPriceFeed` is explicitly deployed as a Chainlink-compatible price feed (confirmed by the README listing it as `RSETHPriceFeed (Morph)`). Any protocol on Morph that consumes this feed and applies a standard staleness guard on `updatedAt` will receive a composite price that is:

- Fresh in the ETH/USD dimension (passes staleness check)
- Stale in the rsETH/ETH dimension (undetectable)

The contract fails to deliver its promised invariant: that `updatedAt` reflects the freshness of the returned `answer`. This is a **Low** impact — the contract fails to deliver promised returns without direct loss of value within the LRT-rsETH contracts themselves, but enables mispricing in any downstream consumer.

---

### Likelihood Explanation

- `updateRSETHPrice()` is a permissionless public function with no access control, making it susceptible to block stuffing on L2s where block gas limits are lower and stuffing costs are reduced.
- The staleness mismatch exists structurally even without block stuffing — any period of keeper inactivity (high gas, network congestion, keeper failure) produces the same condition.
- The `RSETHPriceFeed` is already deployed on Morph and is intended for use by external protocols.

---

### Recommendation

1. Store a `rsETHPriceUpdatedAt` timestamp in `LRTOracle` and update it alongside `rsETHPrice` in `_updateRsETHPrice()`.
2. In `RSETHPriceFeed.latestRoundData()`, return `min(ethToUSD_updatedAt, rsETHPriceUpdatedAt)` as `updatedAt`, so consumers see the staleness of the least-fresh component.
3. Alternatively, add an explicit staleness revert inside `latestRoundData()` if `rsETHPriceUpdatedAt` exceeds a configurable threshold.

---

### Proof of Concept

```solidity
// Differential fork test (Foundry, Morph fork)
// 1. Record rsETHPrice_0 = LRTOracle.rsETHPrice() at block N
// 2. Roll forward ~1 Chainlink heartbeat worth of blocks while stuffing
//    updateRSETHPrice() calls (fill blocks with dummy txs)
// 3. Let ETH/USD move +5% (simulate via mock or real fork movement)
// 4. Call RSETHPriceFeed.latestRoundData() → (_, answer, _, updatedAt, _)
// 5. Assert: block.timestamp - updatedAt < staleness_threshold  (passes — ETH/USD is fresh)
// 6. Compute reference = LRTOracle.rsETHPrice() * live_ETH_USD / 1e18
//    where live_ETH_USD is fetched directly from ETH_TO_USD.latestRoundData()
// 7. Assert: |answer - reference| / reference > safe_deviation_threshold
//    (passes — rsETH component is stale, ETH/USD moved, composite diverges)
// 8. Confirm: no staleness check on updatedAt alone would have caught this
```

The key lines driving the flaw: [3](#0-2) 

`updatedAt` is set from `ETH_TO_USD.latestRoundData()` at line 68, but `answer` is overwritten at line 69 to include the independently-staleable `RS_ETH_ORACLE.rsETHPrice()`. The two components have no shared freshness guarantee. [2](#0-1) 

`updateRSETHPrice()` is public and unguarded beyond `whenNotPaused`, making it blockable via block stuffing on L2.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```
