Audit Report

## Title
TOCTOU Fee Mismatch in `updateRate()` Causes Revert When LZ Fees Increase Between Estimation and Execution - (File: `contracts/cross-chain/MultiChainRateProvider.sol`)

## Summary
`updateRate()` writes `rate` and `lastUpdated` to storage before entering the send loop, then re-queries `estimateFees()` per receiver inside that loop. If LayerZero relayer fees increase between the off-chain `estimateTotalFee()` view call and the on-chain execution block, the accumulated execution-time fees exceed `msg.value`, causing a revert that rolls back all state changes and leaves every destination chain with a stale rate. No ETH is lost, but the contract fails to deliver its core promised function.

## Finding Description
`estimateTotalFee()` ( [1](#0-0) ) is the documented off-chain helper callers use to determine how much ETH to attach. It sums fees in a single view context.

`updateRate()` writes state unconditionally before the send loop: [2](#0-1) 

Then, for each receiver, `estimateFees()` is re-queried at execution time and the result is forwarded directly to `send()`: [3](#0-2) 

There is no pre-computation of the total execution-time fee, no `require(msg.value >= totalFee)` guard, and no buffer. If any single receiver's fee at execution time exceeds the ETH remaining in the call frame, `send()` reverts. Solidity's revert semantics unwind the earlier writes to `rate` and `lastUpdated`, leaving the on-chain state identical to before the call. The function is callable by any external account with no access restriction: [4](#0-3) 

## Impact Explanation
The contract's sole purpose is to propagate a fresh rate to destination chains. When the revert occurs, `rate` and `lastUpdated` remain at their previous values, so all destination chains continue reading a stale rate until a future successful call. The caller's ETH is returned by the EVM revert, so no funds are lost. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
LayerZero relayer fees are set by off-chain oracles and can change between any two consecutive blocks. No attacker action is required; ordinary network congestion or a routine fee adjustment is sufficient. The vulnerable window is one block (~12 s on Ethereum mainnet). Any unprivileged caller following the documented flow (`estimateTotalFee()` → `updateRate{ value: totalFee }()`) is exposed. The condition is repeatable whenever fees tick upward.

## Recommendation
Pre-compute all per-receiver fees before writing any state, accumulate the total, verify `msg.value >= total`, write state, then execute sends with the pre-computed values, and refund any excess:

```solidity
// 1. Compute fees first
uint256[] memory fees = new uint256[](rateReceiversLength);
uint256 totalFee;
for (uint256 i; i < rateReceiversLength; ++i) {
    (fees[i],) = ILayerZeroEndpoint(layerZeroEndpoint)
        .estimateFees(rateReceivers[i]._chainId, address(this), _payload, false, bytes(""));
    totalFee += fees[i];
}
require(msg.value >= totalFee, "Insufficient fee");

// 2. Write state
rate = latestRate;
lastUpdated = block.timestamp;

// 3. Send with pre-computed fees
for (uint256 i; i < rateReceiversLength; ++i) {
    ILayerZeroEndpoint(layerZeroEndpoint).send{ value: fees[i] }(...);
}

// 4. Refund excess
if (msg.value > totalFee) {
    payable(msg.sender).transfer(msg.value - totalFee);
}
```

## Proof of Concept
1. Deploy a mock `ILayerZeroEndpoint` whose `estimateFees()` returns `F` on the first call (simulating the view context) and `F+1` on all subsequent calls (simulating a fee bump in the next block).
2. Deploy `MultiChainRateProvider` pointing at the mock endpoint and register two receivers.
3. Call `estimateTotalFee()` off-chain → returns `2F`.
4. Call `updateRate{ value: 2F }()`.
5. Inside the loop: first `send()` consumes `F+1`, leaving `2F - (F+1) = F-1` ETH. Second `send()` requires `F+1 > F-1` → reverts.
6. Assert: transaction reverted; `rate` and `lastUpdated` are unchanged from their pre-call values.

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
