Audit Report

## Title
Stale `rsETHPrice` Dispatched Cross-Chain After Auto-Pause Triggered by Downside Protection — (`contracts/cross-chain/RSETHRateProvider.sol`)

## Summary
When `LRTOracle._updateRsETHPrice()` detects a price drop beyond `pricePercentageLimit`, it pauses the protocol and returns early without writing the new lower price to `rsETHPrice`. `RSETHRateProvider.getLatestRate()` reads `rsETHPrice` directly with no pause guard, and `updateRate()` is permissionless. Any caller can therefore broadcast the pre-slashing, inflated rate to all L2 receivers after the emergency pause fires, leaving L2 protocols operating on a stale rate until an admin manually unpauses and re-broadcasts.

## Finding Description
**Root cause — `_updateRsETHPrice()` returns before writing the new price.**

In `contracts/LRTOracle.sol` at lines 277–281, when `isPriceDecreaseOffLimit` is true, the function pauses and returns immediately:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;   // ← exits here
}
``` [1](#0-0) 

The assignment `rsETHPrice = newRsETHPrice` at line 313 is never reached, so `rsETHPrice` retains the pre-slashing (inflated) value indefinitely. [2](#0-1) 

**No pause guard in `RSETHRateProvider.getLatestRate()`.**

`RSETHRateProvider.getLatestRate()` reads `rsETHPrice` directly with no check on whether the oracle is paused:

```solidity
function getLatestRate() public view override returns (uint256) {
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
``` [3](#0-2) 

Notably, the `ILRTOracle` interface does not even expose a `paused()` function, making it impossible for the rate provider to check oracle state through the interface. [4](#0-3) 

**`updateRate()` is permissionless.**

`CrossChainRateProvider.updateRate()` has no role check and no oracle-pause check — any EOA paying the LayerZero fee can call it:

```solidity
function updateRate() external payable nonReentrant {
    uint256 latestRate = getLatestRate();
    ...
    ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(...);
}
``` [5](#0-4) 

**Exploit flow:**
1. A real slashing event causes `newRsETHPrice` to drop more than `pricePercentageLimit` below `highestRsethPrice`.
2. Any EOA calls `updateRSETHPrice()` (public, `whenNotPaused`). The auto-pause fires; `rsETHPrice` is NOT updated.
3. Any EOA immediately calls `rsETHRateProvider.updateRate{value: lzFee}()`. The stale, inflated rate is dispatched to all L2 receivers.
4. L2 protocols (lending pools, AMMs) operate on the inflated rate until an admin unpauses L1 and re-broadcasts the correct rate.

`updateRSETHPrice()` carries `whenNotPaused`, so once the pause fires, no further price updates can reach L2 through the normal path — only admin via `updateRSETHPriceAsManager()` can correct it. [6](#0-5) 

## Impact Explanation
**Medium — Temporary freezing of funds.** After a slashing event triggers the auto-pause, L2 pools receive and continue to use the pre-slashing inflated rsETH/ETH rate. On L2 lending protocols, rsETH collateral is overvalued, allowing borrowers to draw more debt than the actual backing supports, creating undercollateralization and bad debt for L2 lenders. On AMMs, liquidity is priced at the wrong rate, enabling arbitrage that drains the pool. In both cases, user funds are at risk and effectively mispriced until an admin unpauses L1 and re-broadcasts the correct rate — a window that could span hours or days depending on admin response time.

## Likelihood Explanation
Both trigger functions are permissionless: `updateRSETHPrice()` is `public` with no role restriction, and `updateRate()` is `external payable` with no role restriction. A real slashing event (or any oracle price movement beyond `pricePercentageLimit`) is sufficient to trigger the auto-pause. An attacker or keeper will call `updateRate()` immediately after, requiring no privileged access and only the LayerZero message fee. The condition is repeatable any time a qualifying price drop occurs.

## Recommendation
**Option A (preferred):** Add a pause guard to `RSETHRateProvider.getLatestRate()`. Since `ILRTOracle` does not expose `paused()`, either extend the interface or cast directly:

```solidity
function getLatestRate() public view override returns (uint256) {
    require(!LRTOracle(rsETHPriceOracle).paused(), "Oracle paused");
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
```

**Option B:** In `_updateRsETHPrice()`, write `rsETHPrice = newRsETHPrice` before calling `_pause()` and `return`, so the stored price always reflects the latest computed value even when the pause fires:

```solidity
if (isPriceDecreaseOffLimit) {
    rsETHPrice = newRsETHPrice;   // update first
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

## Proof of Concept
```solidity
// Foundry fork test
function test_staleRateBroadcastAfterAutoPause() public {
    // 1. Fork mainnet with LRTOracle + RSETHRateProvider deployed
    // 2. Record pre-slashing price
    uint256 preSlashPrice = lrtOracle.rsETHPrice();

    // 3. Set pricePercentageLimit to 1e16 (1%)
    vm.prank(admin);
    lrtOracle.setPricePercentageLimit(1e16);

    // 4. Manipulate underlying asset oracle to simulate >1% price drop
    //    (e.g., mock stETH price oracle to return lower value)
    mockAssetOracle.setPrice(stETH, preSlashPrice * 98 / 100);

    // 5. Any EOA triggers updateRSETHPrice
    vm.prank(address(0xdead));
    lrtOracle.updateRSETHPrice();

    // 6. Oracle is now paused, rsETHPrice NOT updated
    assertTrue(lrtOracle.paused());
    assertEq(lrtOracle.rsETHPrice(), preSlashPrice); // stale!

    // 7. Any EOA broadcasts the stale rate to L2
    vm.deal(address(0xdead), 1 ether);
    vm.prank(address(0xdead));
    rsETHRateProvider.updateRate{value: 0.1 ether}();

    // 8. Rate dispatched to L2 is the inflated pre-slashing value
    assertEq(rsETHRateProvider.rate(), preSlashPrice);
    // L2 receiver now holds the stale inflated rate
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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

**File:** contracts/interfaces/ILRTOracle.sol (L28-32)
```text
    // methods
    function getAssetPrice(address asset) external view returns (uint256);
    function assetPriceOracle(address asset) external view returns (address);
    function rsETHPrice() external view returns (uint256);
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
