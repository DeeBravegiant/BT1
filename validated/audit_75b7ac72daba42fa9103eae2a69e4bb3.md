Audit Report

## Title
`CrossChainRateReceiver.getRate()` Returns Zero Before First `lzReceive`, Causing Division-by-Zero Panic in All Pool Deposits - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver.rate` is a plain `uint256` storage variable that is zero-initialized at deployment. `getRate()` returns this value unconditionally with no zero-check. Every pool variant calls `getRate()` and immediately divides by the result in `viewSwapRsETHAmountAndFee`, triggering a Solidity 0.8 `Panic(0x12)` division-by-zero revert on any `deposit()` call until the first successful `lzReceive` message is processed.

## Finding Description

`CrossChainRateReceiver.rate` is declared as `uint256 public rate` with no initializer, defaulting to `0`. [1](#0-0) 

`getRate()` returns this value with no zero-check or staleness check: [2](#0-1) 

The only write path to `rate` is `lzReceive`, which requires `msg.sender == layerZeroEndpoint`, `_srcChainId == srcChainId`, and `srcAddress == rateProvider` — all of which must be correctly configured and a valid LZ message must have been delivered: [3](#0-2) 

`RSETHRateReceiver`'s constructor sets configuration parameters but never seeds `rate` to a non-zero value: [4](#0-3) 

Every pool variant (`RSETHPool`, `RSETHPoolV2`, `RSETHPoolV3`, and their derivatives) calls `getRate()` and divides by the result with no zero-guard: [5](#0-4) [6](#0-5) [7](#0-6) 

In Solidity 0.8+, `amountAfterFee * 1e18 / 0` triggers `Panic(0x12)`, reverting the entire `deposit()` call. The `limitDailyMint` modifier also calls `viewSwapRsETHAmountAndFee` before the deposit body executes, so the revert occurs even earlier in the call stack. [8](#0-7) 

## Impact Explanation

All pool `deposit()` calls revert with a division-by-zero panic for the entire window between deployment and the first successful `lzReceive`. No user funds are lost (the revert occurs before any transfer), but the pool cannot deliver its promised swap service. This matches the **Low** allowed impact: *Contract fails to deliver promised returns, but doesn't lose value*.

## Likelihood Explanation

The vulnerable window is inherent to every fresh deployment — it exists from contract creation until the first valid LZ message is received. Any unprivileged user attempting to call `deposit()` during this window will trigger the revert. The window is extended indefinitely if: the first LZ message is dropped due to network congestion or insufficient gas; `layerZeroEndpoint`, `srcChainId`, or `rateProvider` is misconfigured at deploy time causing every `lzReceive` to revert on the require checks; or `updateRate()` on the provider side is not called promptly. There is no fallback, no initial rate seed, and no admin function to manually set `rate` on the receiver.

## Recommendation

1. Add a zero-check in `getRate()` in `CrossChainRateReceiver`:
   ```solidity
   function getRate() external view returns (uint256) {
       require(rate != 0, "Rate not initialized");
       return rate;
   }
   ```
2. Alternatively, add an owner-callable `setRate(uint256)` function to `CrossChainRateReceiver` so the rate can be bootstrapped before the first LZ message arrives.
3. All pool `viewSwapRsETHAmountAndFee` implementations should defensively check `rsETHToETHrate != 0` before dividing.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/cross-chain/RSETHRateReceiver.sol";

contract ZeroRateTest is Test {
    RSETHRateReceiver receiver;

    function setUp() public {
        receiver = new RSETHRateReceiver(
            101,
            address(0xDEAD),
            address(0xBEEF)
        );
    }

    function test_getRateIsZeroBeforeFirstMessage() public {
        assertEq(receiver.getRate(), 0);
    }

    function test_poolDepositRevertsOnZeroRate() public {
        uint256 amountAfterFee = 1 ether;
        uint256 rsETHToETHrate = receiver.getRate(); // == 0
        vm.expectRevert(); // Panic(0x12): division by zero
        uint256 result = amountAfterFee * 1e18 / rsETHToETHrate;
        (result);
    }
}
```

Deploy `RSETHRateReceiver` with any valid-looking constructor arguments and call no `lzReceive`. `test_getRateIsZeroBeforeFirstMessage` passes confirming `rate == 0`. `test_poolDepositRevertsOnZeroRate` confirms the `Panic(0x12)` revert, proving `deposit()` is non-functional until a valid LZ message is received.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L12-13)
```text
    /// @notice Last rate updated on the receiver
    uint256 public rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-95)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");

        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L10-15)
```text
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
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

**File:** contracts/pools/RSETHPoolV2.sol (L225-233)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L311-319)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-307)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
