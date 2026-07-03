Audit Report

## Title
Missing Zero-Value Check on Incoming Rate in `lzReceive()` Allows Zero Rate to Be Stored — (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.lzReceive()` decodes and stores an incoming rate from LayerZero with no non-zero guard. Because `LRTOracle.rsETHPrice` initializes to `0` and `updateRate()` on the provider has no access control, any external caller can dispatch a zero rate to the receiver before the oracle is initialized. Once stored, a zero rate causes all L2 pool `deposit()` calls to revert.

## Finding Description
In `CrossChainRateReceiver.lzReceive()`, the decoded rate is stored unconditionally:

```solidity
uint256 _rate = abi.decode(_payload, (uint256));
rate = _rate;          // no require(_rate != 0)
lastUpdated = block.timestamp;
```

`RSETHMultiChainRateProvider.getLatestRate()` reads `rsETHPrice` directly from `LRTOracle`:

```solidity
function getLatestRate() public view override returns (uint256) {
    return ILRTOracle(rsETHPriceOracle).rsETHPrice();
}
```

`LRTOracle.rsETHPrice` is a plain storage variable that starts at `0` (Solidity default) and is only set after `updateRSETHPrice()` is called. `MultiChainRateProvider.updateRate()` is `external payable nonReentrant` with no access control, so any address can call it. If called before `updateRSETHPrice()` has ever executed on L1, it encodes and sends `0` via LayerZero. The receiver stores `rate = 0` without rejection.

With `rate = 0` stored, every pool's deposit path fails:
- **RSETHPoolV2 / RSETHPoolV2ExternalBridge**: `viewSwapRsETHAmountAndFee()` computes `amountAfterFee * 1e18 / rsETHToETHrate`, which panics with division-by-zero.
- **RSETHPoolV3 / RSETHPoolV3ExternalBridge / RSETHPoolV3WithNativeChainBridge**: an explicit guard reverts with `UnsupportedOracle`.

## Impact Explanation
When `rate = 0` is stored in `CrossChainRateReceiver`, every call to `deposit()` on the associated L2 pool reverts. Users cannot exchange ETH or supported LSTs for rsETH/wrsETH on L2. No funds already held by users are lost; the contract simply fails to deliver its promised deposit service. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
The attack window is the period between L2 pool deployment and the first successful call to `updateRSETHPrice()` on L1. During this window `rsETHPrice == 0` and `updateRate()` is callable by any unprivileged address with enough ETH to cover the LayerZero fee. After initialization the price is always ≥ `1 ether` (the `rsethSupply == 0` branch sets `rsETHPrice = 1 ether`), so the window closes permanently. Likelihood is **Low**, but the window is real and exploitable by any external caller during deployment.

## Recommendation
Add a non-zero guard in `lzReceive()` before storing the rate:

```solidity
uint256 _rate = abi.decode(_payload, (uint256));
require(_rate != 0, "CrossChainRateReceiver: zero rate");
rate = _rate;
```

Optionally, add the same guard in `MultiChainRateProvider.updateRate()` (and `CrossChainRateProvider.updateRate()`) to prevent a zero rate from ever being dispatched cross-chain.

## Proof of Concept
1. Protocol is deployed on L1 and L2; `updateRSETHPrice()` has not yet been called, so `LRTOracle.rsETHPrice == 0`.
2. Attacker calls `RSETHMultiChainRateProvider.updateRate{value: fee}()` — no access control check.
3. `getLatestRate()` returns `0`; the provider encodes it and sends it to `RSETHRateReceiver` via the LayerZero endpoint.
4. `CrossChainRateReceiver.lzReceive()` executes: `rate = 0` is stored with no revert.
5. Any user calling `deposit()` on the L2 pool triggers `viewSwapRsETHAmountAndFee()`, which either panics (division by zero in V2 pools) or reverts with `UnsupportedOracle` (V3 pools).
6. The L2 pool is effectively frozen for deposits until an admin sends a valid rate message or directly updates the oracle.

**Foundry test sketch:**
```solidity
// Fork L1 before updateRSETHPrice() is called
// Assert lrtOracle.rsETHPrice() == 0
// Call multiChainRateProvider.updateRate{value: fee}() from attacker EOA
// Simulate lzReceive() on the receiver with payload = abi.encode(0)
// Assert receiver.rate() == 0
// Call pool.deposit{value: 1 ether}() and expect revert (panic or UnsupportedOracle)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-111)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L307-315)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L391-393)
```text
        // Rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();
        if (rsETHToETHrate == 0) revert UnsupportedOracle();
```
