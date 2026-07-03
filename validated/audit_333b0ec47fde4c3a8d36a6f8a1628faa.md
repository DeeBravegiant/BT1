Audit Report

## Title
Excess ETH Permanently Locked in `updateRate` Due to Missing Refund - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` is `payable` and accepts arbitrary ETH from callers, but forwards only the exact `estimatedFee` per receiver to LayerZero rather than `msg.value`. The contract contains no ETH withdrawal or sweep function, so any `msg.value` exceeding the sum of per-receiver fees is permanently locked in the contract. This contrasts directly with `CrossChainRateProvider.updateRate()`, which correctly passes `msg.value` to LayerZero and relies on its built-in refund mechanism.

## Finding Description
In `MultiChainRateProvider.updateRate()`, for each receiver the contract calls `estimateFees()` and then passes only that exact amount to `ILayerZeroEndpoint.send`:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [1](#0-0) 

The `payable(msg.sender)` refund address passed to LayerZero only triggers a refund if LayerZero itself receives surplus ETH. Since the contract sends exactly `estimatedFee` — not `msg.value` — LayerZero receives no surplus and issues no refund. The difference `msg.value − Σ(estimatedFee_i)` accumulates in `MultiChainRateProvider`.

The entire contract contains no ETH recovery path: no `withdraw`, no `sweep`, no `receive`/`fallback` that rejects ETH, and no owner rescue function. [2](#0-1) 

By contrast, `CrossChainRateProvider.updateRate()` passes `msg.value` directly to LayerZero:

```solidity
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [3](#0-2) 

This confirms the discrepancy is a code defect in `MultiChainRateProvider`, not an intentional design choice.

## Impact Explanation
Any ETH sent in excess of the exact sum of per-receiver LayerZero fees is permanently frozen inside `MultiChainRateProvider` with no recovery mechanism. This constitutes **permanent freezing of funds** (Critical), as the locked ETH is irrecoverable by the caller, the owner, or any other party.

## Likelihood Explanation
`updateRate()` has no access control and is callable by any external account. [4](#0-3) 

While `estimateTotalFee()` exists as a helper, fee estimates are point-in-time and can change between the estimation call and the `updateRate()` call. The receiver list can also change via `addRateReceiver`/`removeRateReceiver`, making exact off-chain estimation error-prone. [5](#0-4) 

Callers who add any buffer to guard against fee fluctuations — a standard and expected practice — will permanently lose the buffered ETH on every call.

## Recommendation
After the loop, refund any remaining contract balance to `msg.sender`:

```solidity
function updateRate() external payable nonReentrant {
    // ... existing loop ...

    uint256 remaining = address(this).balance;
    if (remaining > 0) {
        (bool success,) = payable(msg.sender).call{ value: remaining }("");
        require(success, "ETH refund failed");
    }

    emit RateUpdated(rate);
}
```

Alternatively, mirror `CrossChainRateProvider` by splitting `msg.value` proportionally across receivers and passing the full allocated amount to each `send` call, relying on LayerZero's own refund mechanism.

## Proof of Concept
1. Deploy `MultiChainRateProvider` with 3 configured rate receivers.
2. Call `estimateTotalFee()` → returns `0.024 ether` (e.g., `0.008 ether` × 3).
3. Call `updateRate{ value: 0.030 ether }()` (caller adds a 25% buffer).
4. The loop sends `0.008 ether` × 3 = `0.024 ether` to LayerZero across 3 `send` calls.
5. `address(MultiChainRateProvider).balance` is now `0.006 ether`.
6. No function exists to recover it; `0.006 ether` is permanently locked.
7. Repeat on every `updateRate` call with any buffer → ETH accumulates indefinitely.

A Foundry fork test can confirm this by asserting `address(provider).balance > 0` after the call and verifying no withdrawal path exists in the ABI.

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

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L96-98)
```text
        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );
```
