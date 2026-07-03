### Title
Excess ETH Permanently Locked in `MultiChainRateProvider` When `updateRate()` Overpays LayerZero Fees - (File: contracts/cross-chain/MultiChainRateProvider.sol)

---

### Summary

`MultiChainRateProvider.updateRate()` is a public payable function that accepts ETH to cover LayerZero messaging fees across multiple destination chains. It forwards only the exact `estimatedFee` per receiver to LayerZero, but any excess `msg.value` beyond the sum of all per-receiver fees is permanently locked in the contract with no recovery mechanism.

---

### Finding Description

In `MultiChainRateProvider.updateRate()`, the function iterates over all `rateReceivers` and for each one calls `estimateFees()` on-chain at execution time, then sends exactly that `estimatedFee` to the LayerZero endpoint:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [1](#0-0) 

Callers are expected to pre-fund the call by invoking `estimateTotalFee()` off-chain before submitting the transaction. However, LayerZero fees fluctuate with gas prices on destination chains. If fees decrease between the off-chain estimation and on-chain execution, the contract consumes less than `msg.value` and the remainder is silently retained by `MultiChainRateProvider`. [2](#0-1) 

The contract has no `receive()` fallback, no `withdraw()`, and no ETH recovery function. The only state-mutating functions are `updateLayerZeroEndpoint`, `addRateReceiver`, `removeRateReceiver` (all `onlyOwner`), and `updateRate` (public). None of them drain the contract's ETH balance. [3](#0-2) 

This contrasts with `CrossChainRateProvider.updateRate()`, which correctly passes the full `msg.value` directly to LayerZero (allowing LayerZero to refund the excess to `payable(msg.sender)`):

```solidity
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [4](#0-3) 

`MultiChainRateProvider` does not replicate this pattern; it sends only `estimatedFee` per receiver and retains the rest.

---

### Impact Explanation

Any ETH sent in excess of the sum of per-receiver `estimatedFee` values at execution time is permanently locked in `MultiChainRateProvider`. There is no owner-callable recovery function and no `receive`/`fallback` that could be used to drain the balance. The impact is **permanent freezing of funds**.

---

### Likelihood Explanation

LayerZero fee estimates are gas-price-sensitive and change block-to-block. Callers routinely add a safety buffer on top of `estimateTotalFee()` to avoid reverts (a standard practice for cross-chain fee payments). Any such buffer, or any drop in destination-chain gas prices between estimation and inclusion, causes excess ETH to be locked. This is a normal operating condition, not an edge case.

---

### Recommendation

After the loop, refund any remaining ETH balance to `msg.sender`:

```solidity
// After the loop
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool success,) = payable(msg.sender).call{ value: remaining }("");
    require(success, "Refund failed");
}
```

Alternatively, mirror the pattern used in `CrossChainRateProvider` by passing `msg.value` directly to each `send` call and relying on LayerZero's built-in refund to `payable(msg.sender)`.

---

### Proof of Concept

1. Caller invokes `estimateTotalFee()` off-chain for 3 receivers and receives `0.009 ETH`.
2. Caller sends `0.011 ETH` (buffer) to `updateRate()`.
3. At execution time, destination-chain gas prices have dropped; `estimateFees()` returns `0.002 ETH` per receiver (total `0.006 ETH`).
4. The loop sends `0.006 ETH` total to LayerZero across 3 calls.
5. The remaining `0.005 ETH` (`0.011 - 0.006`) is retained by `MultiChainRateProvider`.
6. No function exists to recover it; the ETH is permanently locked. [2](#0-1)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L59-102)
```text
    /// @notice Updates the LayerZero Endpoint address
    /// @dev Can only be called by owner
    /// @param _layerZeroEndpoint the new layer zero endpoint address
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }

    /// @notice Adds a rate receiver
    /// @dev Can only be called by owner
    /// @param _chainId rate receiver chainId
    /// @param _contract rate receiver address
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

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L96-98)
```text
        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );
```
