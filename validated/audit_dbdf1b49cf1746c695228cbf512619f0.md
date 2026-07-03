### Title
Stale `rsETHPrice` Masked by Fresh ETH/USD `updatedAt` in `RSETHPriceFeed` — (`contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.latestRoundData()` and `getRoundData()` return `updatedAt` sourced entirely from the ETH/USD Chainlink feed, while the `answer` is computed using `RS_ETH_ORACLE.rsETHPrice()` — a storage variable in `LRTOracle` that is updated independently via `updateRSETHPrice()`. There is no staleness check or timestamp tracking for the rsETH component. Consumers checking `updatedAt` to detect a stale feed will see a fresh ETH/USD timestamp while silently consuming an arbitrarily old rsETH/ETH rate.

---

### Finding Description

In `RSETHPriceFeed.latestRoundData()`:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
``` [1](#0-0) 

`updatedAt` is taken verbatim from the ETH/USD Chainlink feed. The `answer` is the product of two independent values:

- `ETH_TO_USD` price — updated by Chainlink on its own heartbeat.
- `RS_ETH_ORACLE.rsETHPrice()` — `LRTOracle.rsETHPrice`, a storage variable updated only when `LRTOracle.updateRSETHPrice()` is called. [2](#0-1) [3](#0-2) 

`updateRSETHPrice()` is `public whenNotPaused`, so it depends on an off-chain keeper (or any caller) to invoke it regularly. If the keeper fails, or if `LRTOracle` is paused, `rsETHPrice` freezes at its last stored value indefinitely. No timestamp is stored alongside `rsETHPrice` to track its age.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` explicitly validates `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` before trusting the Chainlink answer: [4](#0-3) 

`RSETHPriceFeed` applies none of these guards to the rsETH component.

---

### Impact Explanation

Any downstream protocol (e.g., a lending market) that uses `RSETHPriceFeed` as a Chainlink-compatible rsETH/USD feed and relies on `updatedAt` for staleness detection will:

- Observe a fresh `updatedAt` (driven by the ETH/USD heartbeat, typically 1 h on mainnet).
- Consume a potentially hours- or days-old rsETH/ETH rate embedded in `answer`.

Two concrete fund-impact scenarios:

1. **Stale-high rsETH price** (rsETH has depreciated but `rsETHPrice` was not updated): valid liquidations are blocked because collateral appears over-valued. Lenders' funds are temporarily frozen — they cannot recover principal through liquidation until the price is corrected.
2. **Stale-low rsETH price** (rsETH has appreciated but `rsETHPrice` was not updated): borrowers are liquidated at an incorrect rate, losing collateral they should have retained — a temporary, partial loss of user funds.

Both map to **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

`updateRSETHPrice()` is keeper-driven. Keeper outages, network congestion, or a paused `LRTOracle` (which blocks `updateRSETHPrice()` via `whenNotPaused`) are realistic operational events. The ETH/USD Chainlink feed continues updating independently during any such outage, so the divergence between `updatedAt` and the actual rsETH price age grows silently. No on-chain mechanism alerts consumers or reverts the call.

---

### Recommendation

1. **Store a `rsETHPriceUpdatedAt` timestamp** in `LRTOracle` alongside `rsETHPrice`, set to `block.timestamp` inside `_updateRsETHPrice()`.
2. **Expose it through `IRSETHOracle`** and read it in `RSETHPriceFeed`.
3. **Return the minimum of the two `updatedAt` values** (ETH/USD and rsETH) so that `updatedAt` reflects the staleness of the composite price:

```solidity
uint256 rsEthUpdatedAt = RS_ETH_ORACLE.rsETHPriceUpdatedAt();
updatedAt = updatedAt < rsEthUpdatedAt ? updatedAt : rsEthUpdatedAt;
```

4. Optionally add a revert guard (mirroring `ChainlinkOracleForRSETHPoolCollateral`) if `rsEthUpdatedAt` is zero or older than a configurable heartbeat.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

// Assumptions (local fork or mock setup):
//   - mockEthUsd: a mock AggregatorV3 that advances updatedAt with block.timestamp
//   - mockRsEthOracle: returns a fixed rsETHPrice (simulating keeper failure)
//   - RSETHPriceFeed deployed with these two mocks

function test_staleRsEthPriceMaskedByFreshUpdatedAt() public {
    uint256 initialPrice = rsEthPriceFeed.latestRoundData().answer; // e.g. 1.05e8 USD

    // Advance time by 48 hours without calling LRTOracle.updateRSETHPrice()
    vm.warp(block.timestamp + 48 hours);

    // ETH/USD mock updates its own updatedAt to block.timestamp
    mockEthUsd.setUpdatedAt(block.timestamp);

    (,int256 answer,, uint256 updatedAt,) = rsEthPriceFeed.latestRoundData();

    // updatedAt is fresh (within last block), but answer still uses 48-hour-old rsETHPrice
    assertEq(updatedAt, block.timestamp);          // appears fresh
    assertEq(answer, initialPrice);                // stale rsETH component unchanged

    // A consumer's staleness check passes; it acts on a 48-hour-old rsETH/USD rate.
}
```

The fuzz variant advances `block.timestamp` by arbitrary `delta` values without calling `updateRSETHPrice()` and asserts that `answer` never changes while `updatedAt` tracks `block.timestamp` — proving the divergence is unbounded.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L68-69)
```text
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```
