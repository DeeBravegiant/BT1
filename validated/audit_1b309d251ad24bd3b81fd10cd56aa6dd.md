Audit Report

## Title
Stale Inflated rsETH Rate Broadcastable Cross-Chain During Oracle Auto-Pause — (`contracts/cross-chain/RSETHRateProvider.sol` / `contracts/cross-chain/CrossChainRateProvider.sol`)

## Summary
When `LRTOracle._updateRsETHPrice()` detects a price drop beyond `pricePercentageLimit`, it pauses the protocol and returns early, leaving `rsETHPrice` at its pre-pause (inflated) value. Because `CrossChainRateProvider.updateRate()` is permissionless and neither `getLatestRate()` nor `updateRate()` checks the oracle's paused state, any caller can immediately broadcast this stale inflated rate to L2 receivers via LayerZero, causing L2 pools to misprice rsETH until an admin manually unpauses and re-broadcasts.

## Finding Description

**Root cause 1 — Early return skips `rsETHPrice` write:**
In `_updateRsETHPrice()`, when `isPriceDecreaseOffLimit` is true, the function pauses and returns at line 281 before reaching the `rsETHPrice = newRsETHPrice` assignment at line 313. The storage variable retains the last valid (higher) pre-pause value. [1](#0-0) [2](#0-1) 

**Root cause 2 — `getLatestRate()` reads `rsETHPrice` without a pause check:**
`RSETHRateProvider.getLatestRate()` unconditionally returns `ILRTOracle(rsETHPriceOracle).rsETHPrice()` with no call to `ILRTOracle(rsETHPriceOracle).paused()`. [3](#0-2) 

**Root cause 3 — `updateRate()` is permissionless with no pause guard:**
`CrossChainRateProvider.updateRate()` is `external payable nonReentrant` — no role check, no oracle-pause check. Any EOA supplying ETH for LayerZero gas can call it immediately after the auto-pause fires. [4](#0-3) 

**Root cause 4 — Recovery requires privileged `unpause()`:**
`LRTOracle.unpause()` is gated to `onlyLRTAdmin`, so the stale rate window persists until an admin acts. [5](#0-4) 

**Exploit flow:**
1. A market event causes `newRsETHPrice` to drop beyond `pricePercentageLimit`.
2. Any caller invokes `LRTOracle.updateRSETHPrice()` (public, no role required) — auto-pause fires, `rsETHPrice` is NOT updated.
3. Attacker immediately calls `RSETHRateProvider.updateRate{value: lzFee}()` — reads the stale inflated `rsETHPrice`, stores it as `rate`, and sends it cross-chain via LayerZero.
4. L2 receiver contract updates its rate to the inflated value.
5. L2 pools misprice rsETH (inflated ETH value per rsETH) until admin unpauses and re-broadcasts.

No existing guard prevents this: `updateRate()` only carries `nonReentrant`, and `getLatestRate()` performs no state check beyond reading the storage variable. [6](#0-5) 

## Impact Explanation
**Medium — Temporary freezing of funds / mispricing of L2 operations.** The L2 receiver holds an inflated rsETH/ETH rate from the moment of auto-pause until an admin unpauses `LRTOracle` and a correct rate is re-broadcast. During this window, L2 pool operations (redemptions, swaps, deposits) execute against a rate that is higher than the true price. Depending on how the L2 receiver is consumed, this can escalate to direct theft from L2 liquidity pools (Critical), but the minimum concrete impact within this repository's scope is temporary mispricing/disruption of L2 rate-dependent operations.

## Likelihood Explanation
- The trigger is a normal market event (price drop beyond `pricePercentageLimit`), not an attack.
- `updateRate()` requires no role, no signature — only ETH for LayerZero fees, which is trivially available.
- The stale rate is already stored in `rsETHPrice`; no manipulation of any oracle or contract state is needed by the attacker.
- The window is bounded only by admin response time, which can be hours to days.

## Recommendation

1. **Add an oracle-pause check in `getLatestRate()`:**
```solidity
function getLatestRate() public view override returns (uint256) {
    require(!ILRTOracle(rsETHPriceOracle).paused(), "Oracle paused");
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
```

2. **Restrict `updateRate()` to a trusted keeper role** (e.g., `onlyOwner` or a dedicated `RATE_UPDATER_ROLE`) so arbitrary callers cannot broadcast during an emergency window.

3. **Emit a cross-chain "pause" message** when `LRTOracle._pause()` fires, so L2 receivers can autonomously halt rate-dependent operations without waiting for admin re-broadcast.

## Proof of Concept

```solidity
// Foundry fork test outline
function test_staleRateBroadcastAfterAutoPause() public {
    // 1. Mock underlying asset oracle to return a price drop > pricePercentageLimit
    vm.mockCall(assetOracle, abi.encodeWithSelector(IPriceFetcher.getAssetPrice.selector, asset), abi.encode(lowPrice));

    // 2. Call updateRSETHPrice() — triggers auto-pause, rsETHPrice NOT updated
    lrtOracle.updateRSETHPrice();
    assertTrue(lrtOracle.paused());

    uint256 stalePre = lrtOracle.rsETHPrice(); // retains pre-pause inflated value

    // 3. Anyone broadcasts the stale rate cross-chain
    vm.deal(attacker, 1 ether);
    vm.prank(attacker);
    rsethRateProvider.updateRate{value: 0.1 ether}();

    // 4. Rate stored in provider equals stale pre-pause value
    assertEq(rsethRateProvider.rate(), stalePre);

    // 5. Stale rate is higher than the true current price
    // (true price would be newRsETHPrice computed from lowPrice, which was never written)
    assertGt(rsethRateProvider.rate(), /* true current price computed off-chain */);
}
```

The test is reproducible on a mainnet fork by setting `pricePercentageLimit` to a small value and mocking a single asset price oracle to return a sufficiently low price, then verifying that `rsethRateProvider.rate()` equals the pre-pause `rsETHPrice` rather than the true lower price.

### Citations

**File:** contracts/LRTOracle.sol (L143-146)
```text
    function unpause() external whenPaused onlyLRTAdmin {
        paused = false;
        emit Unpaused(msg.sender);
    }
```

**File:** contracts/LRTOracle.sol (L277-282)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-29)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L85-101)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceiver, address(this));

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );

        emit RateUpdated(rate);
    }
```
