### Title
Pause Bypass in RSETHMultiChainRateProvider Allows Stale Rate Broadcast to All L2 Receivers — (`contracts/cross-chain/RSETHMultiChainRateProvider.sol`)

---

### Summary

`RSETHMultiChainRateProvider.getLatestRate()` reads `rsETHPrice` directly from `LRTOracle` storage. Because `updateRate()` has no access control and no pause check, any caller can broadcast the frozen, pre-pause rate to every L2 receiver even while `LRTOracle` is paused — including when the oracle auto-paused itself in response to a detected price drop.

---

### Finding Description

`RSETHMultiChainRateProvider.getLatestRate()` is implemented as:

```solidity
function getLatestRate() public view override returns (uint256) {
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
``` [1](#0-0) 

This is a raw storage read of the `uint256 public rsETHPrice` state variable in `LRTOracle`. It does **not** call `updateRSETHPrice()` and does **not** check `paused`.

`LRTOracle.updateRSETHPrice()` is the only public path that recomputes and writes `rsETHPrice`, and it is gated by `whenNotPaused`:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

`LRTOracle` can auto-pause itself — without any admin action — when a price drop exceeds `pricePercentageLimit`:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [3](#0-2) 

When `_pause()` returns early, `rsETHPrice` is **not** updated to the new (lower) value — it remains at the pre-drop level. The frozen value is the last value written at line 313:

```solidity
rsETHPrice = newRsETHPrice;
``` [4](#0-3) 

`MultiChainRateProvider.updateRate()` is callable by anyone — it has no access control and no pause check:

```solidity
function updateRate() external payable nonReentrant {
    uint256 latestRate = getLatestRate();
    rate = latestRate;
    ...
    ILayerZeroEndpoint(layerZeroEndpoint).send{value: estimatedFee}(...);
``` [5](#0-4) 

The L2 receiver accepts whatever rate arrives and stores it unconditionally:

```solidity
uint256 _rate = abi.decode(_payload, (uint256));
rate = _rate;
``` [6](#0-5) 

---

### Impact Explanation

The auto-pause fires precisely because the true rsETH price has dropped significantly (e.g., slashing). At that moment `rsETHPrice` is frozen at the **pre-drop, inflated** value. Broadcasting this inflated rate to L2 pools means:

- L2 pools that allow rsETH redemption will pay out more ETH per rsETH than the asset is actually worth, draining ETH from the pool at the expense of liquidity providers and other holders.
- L2 pools that allow minting will issue fewer rsETH tokens than they should, harming depositors.

The pause mechanism's explicit purpose is to halt rate propagation during anomalous conditions. This bypass defeats that safety guarantee entirely.

---

### Likelihood Explanation

- The auto-pause path requires no admin action — it is triggered automatically by `_updateRsETHPrice()` whenever a price drop exceeds `pricePercentageLimit`.
- `updateRate()` is permissionless and costs only gas + LayerZero fees, which any attacker can supply.
- The window between auto-pause and manual unpause (by `onlyLRTAdmin`) can be hours or days, giving ample time to exploit.

---

### Recommendation

Add a pause check inside `RSETHMultiChainRateProvider.getLatestRate()` (or inside `updateRate()` in the base contract) that reverts when `LRTOracle` is paused:

```solidity
function getLatestRate() public view override returns (uint256) {
    require(!ILRTOracle(rsETHPriceOracle).paused(), "Oracle paused");
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
```

Alternatively, expose a `pauseAware` rate getter on `LRTOracle` that reverts when paused, and call that instead of reading `rsETHPrice` directly.

---

### Proof of Concept

```solidity
// Fork test (Ethereum mainnet fork)
// 1. Deploy / reference LRTOracle, RSETHMultiChainRateProvider
// 2. Simulate a large TVL drop (e.g., mock asset price oracle returns lower value)
// 3. Call LRTOracle.updateRSETHPrice() → triggers auto-pause, rsETHPrice frozen at old value
// 4. Assert: LRTOracle.paused() == true
// 5. Assert: LRTOracle.rsETHPrice() == stalePriceBeforeDrop  (not the new lower price)
// 6. Call RSETHMultiChainRateProvider.updateRate{value: fee}()
//    → succeeds despite oracle being paused
// 7. Assert: RSETHMultiChainRateProvider.rate() == stalePriceBeforeDrop
// 8. On L2 receiver (or mock): assert rate == stalePriceBeforeDrop
//    while true TVL/supply ratio is significantly lower
// → Broadcast rate diverges from true protocol state, bypassing the pause safety mechanism
```

### Citations

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

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

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-130)
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

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-95)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;
```
