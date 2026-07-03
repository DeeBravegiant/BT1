### Title
Provider-Receiver Rate Desync on Blocked LayerZero Message — (`contracts/cross-chain/CrossChainRateProvider.sol`)

### Summary
`CrossChainRateProvider.updateRate()` unconditionally writes `rate = latestRate` and `lastUpdated = block.timestamp` to storage **before** the LayerZero `send()` call completes delivery on the destination chain. If the LZ message is stored as a blocked payload on the destination (e.g., `lzReceive` runs out of gas), `provider.rate` reflects the new value while `receiver.rate` remains at the old value indefinitely until `retryPayload` is manually called.

### Finding Description

In `CrossChainRateProvider.updateRate()`:

```solidity
rate = latestRate;          // line 90 — local state updated unconditionally
lastUpdated = block.timestamp; // line 92
// ...
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(...); // line 96
emit RateUpdated(rate);     // line 100
``` [1](#0-0) 

The local state update at lines 90–92 is not conditional on successful delivery. The LZ `send()` on the source chain can succeed (transaction commits, `provider.rate` is updated) while the corresponding `lzReceive` on the destination chain reverts (e.g., insufficient gas forwarded via `msg.value`). In LZ v1, a reverted `lzReceive` causes the endpoint to store the payload via `hasStoredPayload` — the message is blocked, not lost, but `receiver.rate` is not updated until `retryPayload` is called.

`CrossChainRateReceiver.lzReceive()` only updates `rate` and `lastUpdated` upon successful delivery:

```solidity
rate = _rate;
lastUpdated = block.timestamp;
``` [2](#0-1) 

This creates a persistent desync: `provider.rate == newRate` while `receiver.rate == oldRate`. The same pattern exists in `MultiChainRateProvider.updateRate()`: [3](#0-2) 

### Impact Explanation

Downstream pools on the destination chain (e.g., Curve, Balancer pools using `RSETHRateReceiver.getRate()`) will serve the stale rate while the source chain believes the update was applied. L2 depositors interacting with rate-dependent pools receive wrong exchange rates. No funds are lost, but the contract fails to deliver the promised up-to-date rate.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation

The trigger is insufficient `msg.value` forwarded to `updateRate()` for destination gas, or transient LZ relayer issues causing `lzReceive` to run out of gas. `updateRate()` has no access control — any caller can invoke it with any `msg.value`. The desync persists until an operator manually calls `retryPayload` on the LZ endpoint. Given the number of deployed receiver chains (Base, Linea, zkSync, Avalanche, Sonic, etc.), the probability of at least one blocked message over time is non-trivial.

### Recommendation

Move the local state update to after a successful send, or add a two-step confirmation pattern. At minimum, emit a distinct event when the LZ send is attempted vs. confirmed, and document that `provider.rate` is "last sent" not "last confirmed received." Consider checking `hasStoredPayload` before accepting `provider.rate` as authoritative.

### Proof of Concept

1. Deploy a mock LZ endpoint that stores (does not deliver) payloads via `hasStoredPayload`.
2. Deploy `RSETHRateProvider` pointing to the mock endpoint.
3. Deploy `RSETHRateReceiver` on a simulated destination.
4. Call `updateRate()` on the provider.
5. Assert: `provider.rate == newRate` (updated), `receiver.rate == 0` (never updated), `receiver.lastUpdated == 0`.
6. The desync is confirmed without `retryPayload` being called.

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

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
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
