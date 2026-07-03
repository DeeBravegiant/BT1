### Title
`MultiChainRateProvider.updateRate()` Reverts When `msg.value` Is Insufficient to Cover All Receiver Fees in the Loop - (File: contracts/cross-chain/MultiChainRateProvider.sol)

### Summary

`MultiChainRateProvider.updateRate()` iterates over all registered `rateReceivers` and calls `ILayerZeroEndpoint.send{ value: estimatedFee }(...)` for each one inside a loop. There is no pre-flight check that `msg.value` is sufficient to cover the cumulative sum of all per-receiver fees. If the caller supplies less ETH than the total required, the transaction reverts mid-loop (or on the first iteration whose fee exceeds the remaining balance), leaving all rate receivers un-updated.

### Finding Description

`updateRate()` in `MultiChainRateProvider` estimates the LayerZero fee for each receiver individually inside the loop and immediately forwards exactly that fee to the endpoint:

```solidity
for (uint256 i; i < rateReceiversLength;) {
    ...
    (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
        .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

    ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
        dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
    );
    ...
}
```

Each `send` call consumes `estimatedFee` from the contract's ETH balance. After each iteration the remaining balance shrinks. If `msg.value < Σ(estimatedFee_i)`, the EVM throws `OutOfFund` on the iteration where the balance is exhausted, reverting the entire transaction. No receiver is updated.

The contract provides `estimateTotalFee()` as a view helper, but there is no on-chain enforcement that `msg.value >= estimateTotalFee()` before the loop begins. Because LayerZero fees are dynamic and can change between the off-chain estimate and the on-chain execution, even a well-intentioned caller can trigger this revert.

### Impact Explanation

When `updateRate()` reverts, none of the L2 rate receivers are updated. The rsETH/ETH exchange rate broadcast to all downstream chains becomes stale. L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, etc.) that depend on this rate for minting wrsETH will continue using the old rate, causing users to receive incorrect rsETH amounts until a successful `updateRate()` call is made. No principal is lost, but the contract fails to deliver its promised cross-chain rate synchronization.

**Severity: Low** — Contract fails to deliver promised returns, but doesn't lose value.

### Likelihood Explanation

`updateRate()` is a public `payable` function with no access control. Any caller — including automated keeper bots — can invoke it. Fee estimation must be done off-chain before the call; if fees rise between estimation and execution (common during network congestion), the call reverts. With multiple receivers registered, the probability of a fee mismatch increases with the number of receivers.

### Recommendation

Add a pre-loop check that `msg.value` is at least the sum of all estimated fees before any `send` call is made:

```solidity
function updateRate() external payable nonReentrant {
    ...
    uint256 totalFee;
    uint256[] memory fees = new uint256[](rateReceiversLength);
    for (uint256 i; i < rateReceiversLength;) {
        (fees[i],) = ILayerZeroEndpoint(layerZeroEndpoint)
            .estimateFees(...);
        totalFee += fees[i];
        unchecked { ++i; }
    }
    require(msg.value >= totalFee, "Insufficient ETH for fees");

    for (uint256 i; i < rateReceiversLength;) {
        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: fees[i] }(...);
        unchecked { ++i; }
    }
    ...
}
```

This mirrors the fix applied to the Caviar `EthRouter.change` bug: compute the exact required amount per operation and validate the total before any ETH is forwarded.

### Proof of Concept

1. Deploy `MultiChainRateProvider` with two rate receivers on different chains.
2. Call `estimateTotalFee()` off-chain → returns `X` wei.
3. Call `updateRate{ value: X - 1 }()` (or call with `X` but fees increase by 1 wei before execution).
4. The first `send` succeeds (consuming `fee_0`). The second `send` attempts to forward `fee_1` but `address(this).balance < fee_1` → EVM reverts with `OutOfFund`.
5. The entire transaction reverts; `rate` and `lastUpdated` are not updated; no receiver gets the new rate. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L152-173)
```text
    /// @notice Estimate the fees of sending an update to all chains/receiver contracts
    /// @return totalEstimatedFee the total estimated fee
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
