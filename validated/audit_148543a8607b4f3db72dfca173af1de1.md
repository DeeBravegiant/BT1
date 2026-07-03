### Title
TOCTOU Fee Mismatch in `updateRate()` Causes Revert When LZ Fees Increase Between Estimation and Execution - (File: `contracts/cross-chain/MultiChainRateProvider.sol`)

### Summary
`updateRate()` re-queries `estimateFees()` per receiver inside the execution loop, while `estimateTotalFee()` queries fees in a separate view call. If LZ relayer fees rise between the two calls, the sum of execution-time fees exceeds `msg.value`, causing a revert that rolls back `rate` and `lastUpdated`, leaving all chains with a stale rate despite the caller having paid the quoted fee.

### Finding Description

`estimateTotalFee()` is the documented off-chain helper for computing how much ETH to send: [1](#0-0) 

Inside `updateRate()`, `rate` and `lastUpdated` are written **before** the send loop: [2](#0-1) 

Then, for each receiver, `estimateFees()` is re-queried at execution time and the result is used as the exact `value` forwarded to `send()`: [3](#0-2) 

There is no buffer, no pre-computation of the total, and no check that `msg.value >= sum(execution-time fees)`. If any single receiver's fee at execution time exceeds the remaining ETH balance of the call, `send()` reverts. Because Solidity reverts unwind all state changes, `rate` and `lastUpdated` are rolled back.

The function has no access control: [4](#0-3) 

### Impact Explanation

Any caller who follows the documented flow (`estimateTotalFee()` → `updateRate{ value: totalFee }()`) can have their transaction revert if LZ fees tick upward between the view call and the execution block. The on-chain `rate` and `lastUpdated` remain at their previous values, so all destination chains continue reading a stale rate. The caller's ETH is returned (no fund loss), matching **Low — Contract fails to deliver promised returns, but doesn't lose value**.

### Likelihood Explanation

LZ relayer fees are set by off-chain oracles and can change between blocks. The window is typically one block (~12 s on Ethereum), but fee bumps during congestion are realistic. No attacker action is required; ordinary network conditions are sufficient to trigger the revert.

### Recommendation

Pre-compute all per-receiver fees before writing any state, accumulate the total, verify `msg.value >= total`, and then execute the sends using the pre-computed values:

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

### Proof of Concept

1. Deploy a mock LZ endpoint whose `estimateFees()` returns `F` on the first call (view context) and `F+1` on subsequent calls (simulating a fee bump).
2. Register two receivers so `estimateTotalFee()` returns `2F`.
3. Call `updateRate{ value: 2F }()`.
4. Inside the loop: first receiver costs `F+1`, second receiver costs `F+1`; total needed = `2F+2 > 2F`.
5. The second `send()` call reverts due to insufficient ETH.
6. Assert: transaction reverted, `rate` unchanged, `lastUpdated` unchanged.

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
