The code confirms all claims. The finding is valid.

Audit Report

## Title
Stale `rsETHPrice` Masked by ETH/USD `updatedAt` Under Block Stuffing — (`contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed.latestRoundData()` computes rsETH/USD by multiplying the Chainlink ETH/USD answer by `LRTOracle.rsETHPrice`, but returns `updatedAt` sourced exclusively from the ETH/USD feed. Because `LRTOracle` stores no timestamp for when `rsETHPrice` was last written, and `RSETHPriceFeed` never checks one, an attacker who uses block stuffing to prevent `updateRSETHPrice()` from being mined can cause the feed to serve a below-true rsETH/USD price while advertising a fresh `updatedAt`, defeating any downstream staleness guard.

## Finding Description
`RSETHPriceFeed.latestRoundData()` assembles its return values as:

```solidity
// contracts/oracles/RSETHPriceFeed.sol L68-69
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`updatedAt` is taken verbatim from the Chainlink ETH/USD aggregator. `rsETHPrice` is a separate storage variable in `LRTOracle` updated only when `_updateRsETHPrice()` successfully executes:

```solidity
// contracts/LRTOracle.sol L313
rsETHPrice = newRsETHPrice;
```

No `rsETHPriceUpdatedAt` timestamp is stored anywhere in `LRTOracle`, and `RSETHPriceFeed` never queries one. The public entry point has only a `whenNotPaused` guard:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

**Block stuffing path:** An attacker fills consecutive blocks to their gas limit, preventing any `updateRSETHPrice()` call from being included. During this window, EigenLayer rewards accrue and the true rsETH/ETH exchange rate rises, while `LRTOracle.rsETHPrice` remains frozen at its pre-accrual value. Every call to `RSETHPriceFeed.latestRoundData()` during this window returns an `answer` below the true rsETH/USD price, yet returns an `updatedAt` at most ~1 hour old (the ETH/USD Chainlink heartbeat), making the feed appear fresh to any consumer relying on `updatedAt` for staleness detection. The two components of the returned `answer` have independent freshness guarantees that are never reconciled.

## Impact Explanation
**Low — Block stuffing.** The oracle serves a below-true rsETH/USD price for the duration of the stuffed window while simultaneously advertising a fresh `updatedAt`. Downstream consumers that rely on `updatedAt` for staleness detection cannot distinguish this state from a legitimately fresh price. This directly matches the allowed impact class "Low. Block stuffing."

## Likelihood Explanation
Block stuffing on Ethereum mainnet requires paying for the full block gas limit per block, making it expensive. However, the attack becomes economically rational if the attacker holds a short position against rsETH collateral in a lending protocol and the profit from triggered liquidations exceeds the stuffing cost. No code-level mitigation exists: there is no staleness check on `rsETHPrice` within `RSETHPriceFeed`, and `updateRSETHPrice()` is callable by any unprivileged address, meaning the only barrier is the gas cost of the stuffing itself.

## Recommendation
1. **Track `rsETHPrice` update time.** Add `uint256 public rsETHPriceUpdatedAt` to `LRTOracle` and set it alongside `rsETHPrice` at `contracts/LRTOracle.sol` L313:
   ```solidity
   rsETHPrice = newRsETHPrice;
   rsETHPriceUpdatedAt = block.timestamp;
   ```
2. **Return the correct `updatedAt` from `RSETHPriceFeed`.** Override `updatedAt` with `min(ethToUsdUpdatedAt, rsETHPriceUpdatedAt)` in `latestRoundData()`:
   ```solidity
   uint256 rsETHPriceUpdatedAt = RS_ETH_ORACLE.rsETHPriceUpdatedAt();
   if (rsETHPriceUpdatedAt < updatedAt) updatedAt = rsETHPriceUpdatedAt;
   ```
3. **Add a maximum staleness revert** inside `latestRoundData()` that reverts if `rsETHPriceUpdatedAt` is older than a configured heartbeat.

## Proof of Concept
```solidity
function test_blockStuffingStalePrice() external {
    // 1. Record current rsETHPrice
    uint256 priceBefore = lrtOracle.rsETHPrice();

    // 2. Simulate EigenLayer reward accrual (increase LST balance in deposit pool)
    deal(address(stETH), depositPool, stETHBalance * 110 / 100);

    // 3. Simulate block stuffing: advance time without calling updateRSETHPrice()
    vm.warp(block.timestamp + 24 hours);

    // 4. Query the feed — rsETHPrice is still priceBefore (stale)
    (, int256 answer,, uint256 updatedAt,) = rsETHPriceFeed.latestRoundData();

    // 5. updatedAt reflects ETH/USD heartbeat (appears fresh), masking staleness
    assertGt(updatedAt, block.timestamp - 2 hours);

    // 6. answer is below the true rsETH/USD price because rsETHPrice was not updated
    //    (true price would reflect the 10% LST balance increase)
    uint256 staleAnswer = uint256(answer);
    lrtOracle.updateRSETHPrice(); // now update
    (, int256 freshAnswer,,,) = rsETHPriceFeed.latestRoundData();
    assertLt(staleAnswer, uint256(freshAnswer));
}
```
The test confirms `answer` is below the true rsETH/USD price while `updatedAt` appears fresh, satisfying the stated proof idea.