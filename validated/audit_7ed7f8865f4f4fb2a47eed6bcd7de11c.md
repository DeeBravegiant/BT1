### Title
Single Failing LayerZero Destination Bricks Rate Updates to All L2 Chains - (File: `contracts/cross-chain/MultiChainRateProvider.sol`)

### Summary
`MultiChainRateProvider.updateRate()` iterates over all registered `rateReceivers` and calls `ILayerZeroEndpoint.send` for each in a bare loop with no error isolation. If any single destination's send reverts — due to insufficient ETH for that leg's fee, a broken receiver contract, or a temporarily unavailable LayerZero path — the entire transaction reverts and **no chain receives the updated rate**.

### Finding Description
`MultiChainRateProvider.updateRate()` loops over the `rateReceivers` array and calls `ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(...)` for each entry without any `try/catch` or per-entry error handling:

```solidity
for (uint256 i; i < rateReceiversLength;) {
    uint16 dstChainId = uint16(rateReceivers[i]._chainId);
    bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

    (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
        .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

    ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
        dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
    );

    unchecked { ++i; }
}
``` [1](#0-0) 

The `estimatedFee` for each chain is computed individually inside the loop, but the total ETH available is whatever `msg.value` the caller sent. If the fee estimate for chain `i` is stale or the LayerZero endpoint reverts for any reason (chain paused, receiver contract broken, fee underestimated), the entire call reverts atomically. All chains — including those whose sends would have succeeded — receive nothing.

The `updateRate` function is `external payable` with no access control, so any caller can attempt it. [2](#0-1) 

### Impact Explanation
`MultiChainRateProvider` pushes the rsETH/ETH exchange rate to L2 pool contracts (e.g., `RSETHPoolV3`). L2 pools use this rate to price ETH→rsETH swaps for users. If `updateRate` is persistently bricked:

- All L2 pools operate on a stale rsETH price.
- Because rsETH is a yield-bearing token that appreciates monotonically, a stale (lower) rate lets L2 users swap ETH for **more rsETH than they are entitled to**, draining yield that belongs to existing rsETH holders.
- Conversely, if the rate is stale after a slashing event, users receive too little rsETH.

This constitutes **permanent freezing of unclaimed yield** for the duration of the outage, and potential **theft of unclaimed yield** from existing holders if the stale rate is exploited.

### Likelihood Explanation
Medium. Concrete triggers include:

1. **Fee underestimation**: LayerZero fee estimates can drift between the `estimateFees` call and the `send` call in the same transaction, causing the send to revert for one chain.
2. **Broken/paused receiver**: If the receiver contract on any registered chain is upgraded, paused, or misconfigured, the LayerZero endpoint will revert when attempting delivery.
3. **Chain-level outage**: If any registered destination chain's LayerZero path is temporarily unavailable, every `updateRate` call fails for all chains until the path recovers or the receiver is removed.

The protocol supports multiple L2 chains simultaneously, making it statistically more likely that at least one chain experiences an issue at any given time.

### Recommendation
Wrap each `ILayerZeroEndpoint.send` call in a `try/catch` block and emit an event on failure, so that a single failing destination does not prevent rate propagation to all other chains:

```solidity
try ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
) {
    // success
} catch (bytes memory reason) {
    emit RateUpdateFailed(dstChainId, rateReceivers[i]._contract, reason);
}
```

Additionally, consider adding a per-receiver removal function callable by the owner so that a permanently broken receiver can be excised without blocking all other chains.

### Proof of Concept

1. Deploy `MultiChainRateProvider` (or any concrete subclass) with two `rateReceivers`: chain A (healthy) and chain B (receiver contract broken/paused).
2. Call `updateRate()` with sufficient ETH.
3. The loop reaches chain B; `ILayerZeroEndpoint.send` reverts because the receiver is broken.
4. The entire transaction reverts — chain A also receives no rate update.
5. Repeat indefinitely: every `updateRate` call reverts until chain B's receiver is fixed or removed (removal requires admin action via `removeRateReceiver`).
6. During this window, L2 pools on chain A price rsETH at the last committed stale rate. As rsETH accrues yield on L1, the gap between the stale L2 rate and the true rate grows, allowing arbitrageurs to swap ETH for rsETH on L2 at a discount, extracting yield from existing holders. [3](#0-2) [2](#0-1)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L72-101)
```text
    function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
        rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));

        emit RateReceiverAdded(_chainId, _contract);
    }

    /// @notice Removes a rate receiver
    /// @dev Can only be called by owner
    /// @param _index the index of the rate receiver
    function removeRateReceiver(uint256 _index) external onlyOwner {
        // Store the rate receiver in a memory var
        RateReceiver memory _rateReceiverToBeRemoved = rateReceivers[_index];

        // Get the current length of all the rate receivers
        uint256 rateReceiversLength = rateReceivers.length;

        // Get the last index of the all the rate receivers
        uint256 lastIndex = rateReceiversLength - 1;

        if (lastIndex != _index) {
            // Get the last rate receiver
            RateReceiver memory lastValue = rateReceivers[lastIndex];

            // Replace the index value with the last index value
            rateReceivers[_index] = lastValue;
        }

        rateReceivers.pop();

        emit RateReceiverRemoved(_rateReceiverToBeRemoved._chainId, _rateReceiverToBeRemoved._contract);
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
