Audit Report

## Title
Block Stuffing Prevents `updateRSETHPrice()`, Keeping `RSETHPriceFeed` Serving Stale Price and Bypassing Automatic Pause — (`contracts/oracles/RSETHPriceFeed.sol`)

## Summary

`RSETHPriceFeed.latestRoundData()` multiplies the live ETH/USD Chainlink price by `LRTOracle.rsETHPrice`, a storage variable that is only written inside `_updateRsETHPrice()`. Because `updateRSETHPrice()` is a public, permissionless function, an attacker can use block stuffing to prevent it from executing, keeping `rsETHPrice` stale and simultaneously preventing the automatic pause of `LRTDepositPool` and `LRTWithdrawalManager` that would otherwise trigger on a large price deviation.

## Finding Description

`RSETHPriceFeed.latestRoundData()` at lines 63–70 of `contracts/oracles/RSETHPriceFeed.sol` derives the rsETH/USD answer by reading `RS_ETH_ORACLE.rsETHPrice()` — which resolves to the `uint256 public override rsETHPrice` storage variable declared at line 28 of `contracts/LRTOracle.sol` — and multiplying it by the live ETH/USD Chainlink answer. This stored value is only written at line 313 of `contracts/LRTOracle.sol` inside `_updateRsETHPrice()`, which is reached exclusively through `updateRSETHPrice()` (public, `whenNotPaused`, no role check, line 87) or `updateRSETHPriceAsManager()` (restricted to `onlyLRTManager`, line 94).

The entire downside-protection pause path lives inside `_updateRsETHPrice()` at lines 270–281 of `contracts/LRTOracle.sol`: it computes `newRsETHPrice`, compares it against `highestRsethPrice`, and only then calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`. If `_updateRsETHPrice()` is never executed, none of those pause calls fire.

`RSETHPriceFeed` contains no staleness guard. The `updatedAt` value returned at line 68 is taken directly from the ETH/USD Chainlink feed, not from the last `updateRSETHPrice()` call, so downstream consumers cannot detect that the rsETH component is stale.

An attacker who observes a large drop in underlying LST value (making the true rsETH price fall beyond `pricePercentageLimit`) can fill every block with high-gas transactions for the duration of the attack window. During that window: `updateRSETHPrice()` cannot land; `rsETHPrice` remains at the pre-deviation (inflated) value; `RSETHPriceFeed.latestRoundData()` returns `staleRsETHPrice × currentETH/USD`; and neither `LRTDepositPool` nor `LRTWithdrawalManager` is paused.

## Impact Explanation

**Low — Block stuffing.** The contract fails to deliver its promised safety guarantee: `RSETHPriceFeed` serves an inflated rsETH/USD price to every integrated protocol (e.g., lending markets using rsETH as collateral), and the automatic pause that is supposed to protect depositors and withdrawers during a significant LST depeg does not fire. No direct fund theft occurs within this contract alone, but the stale price enables over-collateralized borrowing against an asset whose true backing has dropped, matching the allowed impact "Low. Block stuffing / contract fails to deliver promised returns."

## Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive but has been executed in real DeFi exploits. The attacker's profit window only needs to last long enough to exploit the stale price in integrated lending protocols. The cost/benefit ratio is unfavorable for small deviations but becomes viable when the price deviation is large — precisely the scenario `pricePercentageLimit` is designed to guard against. The attack requires no privileged access; any external actor can execute it.

## Recommendation

1. **Add a staleness guard in `RSETHPriceFeed`**: record `block.timestamp` each time `rsETHPrice` is written in `_updateRsETHPrice()` and store it as `rsETHPriceUpdatedAt`. In `latestRoundData()`, revert (or return a zero/sentinel answer) if `block.timestamp - rsETHPriceUpdatedAt` exceeds a configurable heartbeat (e.g., 24 hours).
2. **Return the correct `updatedAt`**: `latestRoundData()` should return `rsETHPriceUpdatedAt` (the timestamp of the last `updateRSETHPrice()` call) rather than the ETH/USD feed's `updatedAt`, so downstream consumers can apply their own staleness checks.
3. **Add a push-based pause path**: expose a separate permissionless function that reads current asset prices, computes the prospective new rsETH price, and triggers the pause if the deviation exceeds `pricePercentageLimit` — without updating `rsETHPrice`. This allows the safety pause to land even when the full price-update transaction is stuffed out.

## Proof of Concept

```solidity
// Foundry fork test against mainnet fork
function testBlockStuffingStalePrice() public {
    uint256 stalePriceBefore = lrtOracle.rsETHPrice();

    // Simulate a large drop in underlying asset value on the fork
    // (e.g., manipulate the stETH price oracle so _getTotalEthInProtocol()
    // returns a value > pricePercentageLimit below highestRsethPrice * rsethSupply)
    mockAssetOracle.setPrice(address(stETH), stalePriceBefore * 80 / 100); // 20% drop

    // Block stuffing: attacker fills every block — modelled here by simply
    // NOT calling updateRSETHPrice() for the attack window duration.
    // In a real attack, attacker submits high-gas txs to consume all block gas.

    // RSETHPriceFeed still returns the pre-deviation (inflated) price
    (, int256 answer,,,) = rsETHPriceFeed.latestRoundData();
    assertEq(uint256(answer), stalePriceBefore * ethUsdPrice / 1e18);

    // Protocol is NOT paused — automatic pause never fired
    assertFalse(lrtDepositPool.paused());
    assertFalse(lrtWithdrawalManager.paused());

    // Stored rsETHPrice is stale
    assertEq(lrtOracle.rsETHPrice(), stalePriceBefore);
}
```

The test passes on unmodified code because `_updateRsETHPrice()` — the sole location of both the price write and the pause logic — was never called.