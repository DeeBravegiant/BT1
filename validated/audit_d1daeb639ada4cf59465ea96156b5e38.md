Audit Report

## Title
Atomic revert in `updateRate()` leaves all cross-chain receivers on stale rate when any single LZ `send()` fails — (`contracts/cross-chain/MultiChainRateProvider.sol`)

## Summary
`MultiChainRateProvider.updateRate()` iterates over all registered `rateReceivers` and calls `ILayerZeroEndpoint.send()` for each one with no `try/catch` and no pre-loop `msg.value` sufficiency check. If any single `send()` reverts due to insufficient ETH balance, the entire transaction reverts atomically, leaving every receiver on the previous stale rate. The `rate` and `lastUpdated` state writes that occur before the loop are also rolled back.

## Finding Description
In `updateRate()` at lines 111–113, `rate` and `lastUpdated` are written before the loop begins: [1](#0-0) 

The loop at lines 119–134 calls `estimateFees` per receiver and immediately forwards exactly that amount to `send()` with no `try/catch`: [2](#0-1) 

There is no guard before the loop asserting `msg.value >= sum(estimatedFees)`. The helper `estimateTotalFee()` exists at lines 154–173 but is purely off-chain advisory with no on-chain enforcement: [3](#0-2) 

The function is permissionless (`external payable nonReentrant`, no role guard): [4](#0-3) 

**Exploit path:**
1. Two or more `RateReceiver` entries are registered.
2. Caller sends `msg.value` sufficient for receiver[0]'s fee but not receiver[1]'s (due to gas price movement between off-chain estimation and block inclusion, or simple miscalculation).
3. `send()` for receiver[0] succeeds and consumes its fee from the contract's balance.
4. `send()` for receiver[1] reverts because the remaining balance is less than `estimatedFee`.
5. The EVM unwinds the entire transaction; both `rate`/`lastUpdated` writes and all `send()` calls are rolled back. All receivers retain the old stale rate.

## Impact Explanation
All registered `RSETHRateReceiver` instances remain on the previous stale rsETH/ETH rate. Downstream liquidity pools or rate-dependent integrations on every destination chain continue to quote the wrong rate until a subsequent successful `updateRate()` call. No funds are lost, but the contract fails to deliver its core promise of propagating fresh rates. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
`updateRate()` is permissionless, so any caller can trigger it. Ethereum mainnet gas prices fluctuate; a fee estimated off-chain seconds before submission can be stale by the time the transaction is mined. With N receivers, the probability of at least one fee being under-estimated grows with N. The failure is silent from the caller's perspective (just a revert), making it easy to miss and repeat.

## Recommendation
1. **Pre-loop balance check:** compute the total estimated fee before the loop and revert early with a descriptive error if `msg.value` is insufficient.
2. **`try/catch` per send:** wrap each `ILayerZeroEndpoint(layerZeroEndpoint).send{...}(...)` in a `try/catch` so a single failure does not roll back successful sends.
3. **Refund excess ETH:** after the loop, return any unspent ETH to `msg.sender`.

## Proof of Concept
```solidity
contract MockLZEndpoint {
    uint256 public callCount;
    function estimateFees(...) external pure returns (uint256, uint256) {
        return (0.01 ether, 0);
    }
    function send(...) external payable {
        callCount++;
        if (callCount == 2) revert("fee too low");
    }
}

function testStaleRateOnPartialFailure() public {
    MockLZEndpoint lz = new MockLZEndpoint();
    RSETHMultiChainRateProvider provider = new RSETHMultiChainRateProvider(oracle, address(lz));
    provider.addRateReceiver(101, receiver1);
    provider.addRateReceiver(102, receiver2);

    // Send ETH only sufficient for one receiver
    vm.expectRevert();
    provider.updateRate{value: 0.01 ether}();

    // Both receivers still hold rate == 0 (stale); state writes were rolled back
    assertEq(provider.rate(), 0);
    assertEq(provider.lastUpdated(), 0);
}
```
Deploy `MockLZEndpoint` locally, register two receivers, call `updateRate` with ETH covering only one fee, and assert that `provider.rate()` and `provider.lastUpdated()` remain at their pre-call values, confirming the atomic rollback.

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-108)
```text
    function updateRate() external payable nonReentrant {
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L111-113)
```text
        rate = latestRate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L124-129)
```text
            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L154-173)
```text
    function estimateTotalFee() external view returns (uint256 totalEstimatedFee) {
        uint256 latestRate = getLatestRate();

        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            totalEstimatedFee += estimatedFee;

            unchecked {
                ++i;
            }
        }
    }
```
