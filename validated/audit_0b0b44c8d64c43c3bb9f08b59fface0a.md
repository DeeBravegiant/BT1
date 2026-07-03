Audit Report

## Title
Zero-Rate Broadcast via Unguarded `updateRate()` Before Oracle Initialization — (`contracts/cross-chain/CrossChainRateProvider.sol`)

## Summary
`CrossChainRateProvider.updateRate()` contains no guard against reading and broadcasting a zero rate. Before `LRTOracle.updateRSETHPrice()` is ever called, `rsETHPrice` holds its EVM-default value of `0`. Any unprivileged caller can invoke `updateRate()` during this window, propagating zero to `CrossChainRateReceiver`, which stores it without validation. Subsequent calls to `RSETHPoolV2.deposit()` then revert with a division-by-zero panic until a valid rate is re-broadcast.

## Finding Description
**Root cause — `updateRate()` unconditionally reads and broadcasts the oracle value:**

`CrossChainRateProvider.updateRate()` reads `getLatestRate()` and sends it over LayerZero with no zero-value check: [1](#0-0) 

`RSETHRateProvider.getLatestRate()` directly reads `rsETHPrice` from `LRTOracle`: [2](#0-1) 

`LRTOracle.rsETHPrice` is a plain `uint256` state variable that defaults to `0` at the EVM level. It is only assigned inside `_updateRsETHPrice()`, which is invoked by `updateRSETHPrice()` or `updateRSETHPriceAsManager()`. Before either is ever called, `rsETHPrice == 0`: [3](#0-2) 

**Root cause — `lzReceive()` stores the received rate without validation:**

`CrossChainRateReceiver.lzReceive()` decodes and stores whatever rate arrives, including zero: [4](#0-3) 

`getRate()` then returns this stored zero: [5](#0-4) 

**Division-by-zero in `RSETHPoolV2`:**

`viewSwapRsETHAmountAndFee()` divides by the oracle rate with no zero guard: [6](#0-5) 

`deposit()` applies the `limitDailyMint` modifier before any state change, which calls `viewSwapRsETHAmountAndFee()` internally: [7](#0-6) [8](#0-7) 

The exploit path is: deploy contracts → attacker calls `updateRate()` before `updateRSETHPrice()` is ever invoked → `getLatestRate()` returns `0` → LayerZero delivers the payload → `CrossChainRateReceiver.rate = 0` → any `deposit()` call panics at `amountAfterFee * 1e18 / 0`. The state persists until `updateRSETHPrice()` is called on L1 and `updateRate()` is called again afterward. No existing checks prevent this: `updateRate()` is `external payable` with only a `nonReentrant` guard, and `lzReceive()` only validates the sender and source chain, not the payload value.

## Impact Explanation
Deposits into `RSETHPoolV2` revert with a Solidity division-by-zero panic for as long as `CrossChainRateReceiver.rate == 0`. No user funds are lost or locked — ETH sent via `deposit()` is simply rejected — but the pool fails to deliver its core swap service. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
The precondition (`rsETHPrice == 0`) exists only during the deployment window before `updateRSETHPrice()` is first called. Once called, `rsETHPrice` is set to a non-zero value and cannot return to zero through normal operation. The attack requires no special role or privilege — `updateRate()` is callable by any EOA willing to pay the LayerZero messaging fee. The window is narrow on a live deployment but the code contains no guard to prevent it.

## Recommendation
Add a zero-rate guard in `updateRate()`:

```solidity
// In CrossChainRateProvider.updateRate():
uint256 latestRate = getLatestRate();
require(latestRate != 0, "Rate cannot be zero");
```

Optionally add a symmetric guard in `CrossChainRateReceiver.lzReceive()`:

```solidity
require(_rate != 0, "Received zero rate");
```

## Proof of Concept
1. Deploy `LRTOracle` via proxy; do **not** call `updateRSETHPrice()` → `rsETHPrice == 0`.
2. Deploy `RSETHRateProvider` pointing to that oracle.
3. Deploy `CrossChainRateReceiver` (or mock `lzReceive` directly in a fork test).
4. Any EOA calls `RSETHRateProvider.updateRate{value: fee}()`.
5. `getLatestRate()` returns `0`; LayerZero delivers the payload; `CrossChainRateReceiver.rate` becomes `0`.
6. Call `RSETHPoolV2.deposit{value: 1 ether}("")` → `limitDailyMint` calls `viewSwapRsETHAmountAndFee` → executes `1e18 / 0` → EVM panic revert.
7. Assert: `receiver.getRate() == 0` and `deposit` reverts with `Panic(0x12)`.

### Citations

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

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-29)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-95)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L72-78)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-207)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV2.sol (L230-233)
```text
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
