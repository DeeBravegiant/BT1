All five code references check out against the actual source. Let me confirm each step:

- `LRTOracle.sol` lines 277–281: auto-pause + early `return` confirmed; `rsETHPrice` at line 313 is never reached. [1](#0-0) 
- `LRTOracle.sol` line 87: `updateRSETHPrice()` gated by `whenNotPaused`, blocking further L1 updates. [2](#0-1) 
- `RSETHMultiChainRateProvider.sol` lines 26–28: `getLatestRate()` reads raw `rsETHPrice()` with no pause check. [3](#0-2) 
- `MultiChainRateProvider.sol` lines 108–137: `updateRate()` is `external payable nonReentrant` with no access control and no oracle-liveness guard. [4](#0-3) 
- `CrossChainRateReceiver.sol` lines 93–97: `lzReceive` stores whatever rate arrives with no staleness check. [5](#0-4) 

Note: `updateRSETHPriceAsManager()` (line 94) bypasses the pause, but it is `onlyLRTManager` — not callable by an unprivileged user. [6](#0-5) 

---

Audit Report

## Title
Stale Frozen `rsETHPrice` Propagated to All L2 Chains When `LRTOracle` Auto-Pauses on Price Drop — (`contracts/cross-chain/RSETHMultiChainRateProvider.sol`)

## Summary
When `LRTOracle._updateRsETHPrice()` detects a price drop beyond `pricePercentageLimit`, it calls `_pause()` and returns early without writing the new price to `rsETHPrice`. The stored `rsETHPrice` is frozen at the last pre-pause value indefinitely. Because `RSETHMultiChainRateProvider.getLatestRate()` reads `rsETHPrice` with no oracle-liveness check, and `MultiChainRateProvider.updateRate()` is permissionless, any caller can broadcast the frozen stale rate to every registered L2 `RSETHRateReceiver`. Yield that accrues on L1 after the pause is never reflected in the L2 rate until an admin manually unpauses and triggers a price update.

## Finding Description
**Root cause:** `LRTOracle._updateRsETHPrice()` (lines 277–281) performs an early `return` after calling `_pause()` when `isPriceDecreaseOffLimit` is true, leaving `rsETHPrice` (line 313) at its last pre-pause value. The public entry point `updateRSETHPrice()` (line 87) is guarded by `whenNotPaused`, so no further L1 price updates are possible through the normal path. The manager-only bypass `updateRSETHPriceAsManager()` (line 94) is not accessible to unprivileged callers.

**Propagation path:**
1. `RSETHMultiChainRateProvider.getLatestRate()` (lines 26–28) calls `ILRTOracle(rsETHPriceOracle).rsETHPrice()` — a plain public state variable getter that does not check `paused`.
2. `MultiChainRateProvider.updateRate()` (lines 108–137) is `external payable nonReentrant` with no access control and no oracle-liveness guard. It reads the frozen rate via `getLatestRate()`, stores it locally, and sends it via LayerZero to every registered receiver.
3. `CrossChainRateReceiver.lzReceive()` (lines 93–97) decodes and stores whatever rate it receives, updating `lastUpdated = block.timestamp`, making the stale rate appear fresh.

**Why existing checks are insufficient:** `nonReentrant` prevents re-entrancy but not stale-rate propagation. There is no check of `ILRTOracle.paused` anywhere in the cross-chain rate path. The `whenNotPaused` guard on `updateRSETHPrice()` prevents new L1 updates but does not prevent the frozen value from being read and broadcast.

## Impact Explanation
All L2 pools and wrsETH wrapper contracts consuming the rate from `RSETHRateReceiver` will price rsETH at the frozen pre-pause value. Any yield that accrues on L1 after the pause (staking rewards, EigenLayer rewards, etc.) is not reflected in the L2 rate. wrsETH holders on L2 cannot realize this yield until an admin manually unpauses the oracle and triggers a new price update. There is no automatic unpause mechanism, making the freeze effectively permanent absent admin intervention. This matches **Medium — Permanent freezing of unclaimed yield**.

## Likelihood Explanation
- `pricePercentageLimit` is a standard operational parameter expected to be configured in production.
- A price drop event (e.g., slashing, collateral depeg) is a realistic market scenario.
- `updateRate()` is a permissionless `external payable` function — any caller (keeper bot, well-meaning user, or attacker) can trigger stale propagation at any time after the pause.
- No attacker capability beyond calling a public function and supplying ETH for LayerZero fees is required.
- The condition is repeatable: every call to `updateRate()` while the oracle is paused resets `lastUpdated` on L2 receivers, making the stale rate appear continuously fresh.

## Recommendation
Add an oracle-liveness guard in `RSETHMultiChainRateProvider.getLatestRate()`:

```solidity
function getLatestRate() public view override returns (uint256) {
    require(!ILRTOracle(rsETHPriceOracle).paused(), "Oracle paused: rate stale");
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
```

Alternatively, add the check in `MultiChainRateProvider.updateRate()` before reading the rate. This ensures that when the L1 oracle is paused, stale rates cannot be broadcast to L2 chains. The `ILRTOracle` interface should expose `paused()` as a view function if not already present.

## Proof of Concept
```solidity
// Foundry fork test outline
function test_stalePropagationAfterOraclePause() public {
    // 1. Configure pricePercentageLimit (e.g. 1%)
    vm.prank(admin);
    lrtOracle.setPricePercentageLimit(1e16);

    // 2. Simulate price drop beyond limit via mock asset oracle
    mockAssetOracle.setPrice(assetAddr, currentPrice * 98 / 100);

    // 3. Call updateRSETHPrice() — triggers _pause() + early return
    lrtOracle.updateRSETHPrice();
    assertTrue(lrtOracle.paused());

    uint256 frozenRate = lrtOracle.rsETHPrice(); // pre-pause value, unchanged

    // 4. Time passes; real yield accrues on L1 (oracle stays paused)
    vm.warp(block.timestamp + 7 days);

    // 5. Anyone calls updateRate() — no revert, broadcasts frozen rate
    vm.deal(address(this), 1 ether);
    rsETHMultiChainRateProvider.updateRate{value: 0.1 ether}();

    // 6. Simulate lzReceive delivery on L2 receiver
    vm.prank(layerZeroEndpoint);
    rsETHRateReceiver.lzReceive(
        srcChainId,
        abi.encodePacked(address(rsETHMultiChainRateProvider), address(rsETHRateReceiver)),
        0,
        abi.encode(frozenRate)
    );

    // 7. L2 rate == frozen pre-pause value; lastUpdated == block.timestamp (appears fresh)
    assertEq(rsETHRateReceiver.rate(), frozenRate);
    assertEq(rsETHRateReceiver.lastUpdated(), block.timestamp);
    // Yield delta accrued over 7 days is inaccessible to wrsETH holders on L2
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L277-281)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
```
