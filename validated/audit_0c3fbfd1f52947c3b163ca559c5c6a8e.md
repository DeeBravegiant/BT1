Audit Report

## Title
Permissionless `updateRate()` broadcasts stale pre-pause rsETH price to L2 pools after oracle auto-pause — (`contracts/cross-chain/MultiChainRateProvider.sol`)

## Summary
When `LRTOracle._updateRsETHPrice()` detects a price drop exceeding `pricePercentageLimit`, it auto-pauses the protocol and returns early **without** writing the new lower price to `rsETHPrice`. Because `RSETHMultiChainRateProvider.getLatestRate()` reads `rsETHPrice` directly with no pause guard, and `MultiChainRateProvider.updateRate()` has no access control or pause check, any unprivileged caller can immediately broadcast the stale inflated pre-pause rate to every configured L2 receiver, enabling direct theft of LP funds on L2.

## Finding Description

**Root cause — auto-pause leaves `rsETHPrice` stale:**

`LRTOracle._updateRsETHPrice()` pauses and returns early when a price drop exceeds the threshold:

```solidity
// contracts/LRTOracle.sol L277-281
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;   // rsETHPrice is NOT written
}
``` [1](#0-0) 

`rsETHPrice` (line 28) retains the last written value — the pre-drop inflated price — because `rsETHPrice = newRsETHPrice` at line 313 is never reached in this branch. [2](#0-1) [3](#0-2) 

**`getLatestRate()` reads the stale storage slot unconditionally:**

```solidity
// contracts/cross-chain/RSETHMultiChainRateProvider.sol L26-28
function getLatestRate() public view override returns (uint256) {
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
``` [4](#0-3) 

`rsETHPrice()` is a plain public storage getter — it does not check `paused`. There is no staleness guard anywhere in this call path.

**`updateRate()` is permissionless with no pause check:**

```solidity
// contracts/cross-chain/MultiChainRateProvider.sol L108-137
function updateRate() external payable nonReentrant {
    uint256 latestRate = getLatestRate();
    rate = latestRate;
    ...
    ILayerZeroEndpoint(layerZeroEndpoint).send{value: estimatedFee}(...);
``` [5](#0-4) 

There is no `onlyOwner`, no `whenNotPaused`, and no check that `ILRTOracle.paused == false`. The only guard is `nonReentrant`, which is irrelevant here.

**Complete exploit path:**
1. A market event causes rsETH backing to drop sharply.
2. Anyone calls `LRTOracle.updateRSETHPrice()` (public, `whenNotPaused` passes because oracle is not yet paused). Inside `_updateRsETHPrice()`, the drop exceeds `pricePercentageLimit` → oracle auto-pauses, returns without writing the new lower price.
3. `rsETHPrice` now holds the pre-drop inflated value (e.g. 1.10 ETH when true value is 0.95 ETH).
4. Attacker calls `RSETHMultiChainRateProvider.updateRate{value: lzFee}()`.
5. `getLatestRate()` returns the stale 1.10 value; it is encoded and sent via LayerZero to every L2 rate receiver.
6. L2 pools (Curve, Balancer, Pendle) reprice rsETH at 1.10 ETH.
7. Attacker acquires rsETH on L1 secondary markets at the true ~0.95 ETH price and sells on L2 at the broadcast 1.10 rate, extracting ETH from LP reserves.

## Impact Explanation

**Critical — Direct theft of at-rest user funds.** L2 liquidity pool LPs suffer direct ETH loss. The attacker acquires rsETH below the broadcast rate and redeems/swaps it on L2 at the inflated stale rate, extracting the spread from LP reserves. The L1 deposit pool being paused does not prevent L2-side exploitation; rsETH remains tradeable on L1 secondary markets throughout the pause window.

## Likelihood Explanation

- Auto-pause is triggered by normal market conditions (price drop exceeding `pricePercentageLimit`), not admin action — no collusion or privileged access required.
- `updateRate()` is fully permissionless; the attacker only needs to supply LayerZero fees (a few dollars of ETH).
- The exploit window is open from the moment the oracle auto-pauses until an admin manually unpauses and re-broadcasts a correct rate — potentially hours.
- The scenario is directly incentivized: the larger the price drop that triggered the pause, the larger the spread the attacker can exploit.

## Recommendation

Add an oracle-pause guard to `updateRate()` in `RSETHMultiChainRateProvider` or the base `MultiChainRateProvider`:

```solidity
function updateRate() external payable nonReentrant {
    require(!ILRTOracle(rsETHPriceOracle).paused(), "Oracle paused: rate may be stale");
    uint256 latestRate = getLatestRate();
    ...
}
```

Alternatively, revert inside `getLatestRate()` when the oracle is paused, so no stale rate can ever be read or broadcast regardless of which function calls it.

## Proof of Concept

```solidity
// Foundry fork test (Ethereum mainnet fork)
function testStaleBroadcastAfterAutoPause() public {
    // 1. Mock a large asset price drop so _updateRsETHPrice triggers auto-pause
    vm.mockCall(
        address(assetPriceOracle),
        abi.encodeWithSelector(IPriceFetcher.getAssetPrice.selector, stETH),
        abi.encode(0.80 ether)
    );
    // updateRSETHPrice passes whenNotPaused, then _updateRsETHPrice auto-pauses
    lrtOracle.updateRSETHPrice();

    // 2. Confirm oracle is paused and rsETHPrice is stale (pre-drop inflated value)
    assertTrue(lrtOracle.paused());
    uint256 staleRate = lrtOracle.rsETHPrice(); // still pre-drop value

    // 3. Unprivileged attacker broadcasts stale rate — no privilege needed
    vm.deal(attacker, 1 ether);
    vm.prank(attacker);
    rateProvider.updateRate{value: lzFee}();

    // 4. Rate stored and broadcast equals the stale inflated value
    assertEq(rateProvider.rate(), staleRate);
    // L2 pool now prices rsETH at staleRate > true backing → exploit window open
}
```

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L277-281)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-137)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );

            unchecked {
                ++i;
            }
        }

        emit RateUpdated(rate);
    }
```
