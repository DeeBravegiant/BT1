Audit Report

## Title
Excess ETH Permanently Locked in `updateRate` Due to Per-Receiver Fee Forwarding - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` is a permissionless `payable` function that accepts ETH to cover LayerZero cross-chain fees. For each receiver, it forwards exactly `estimatedFee` to the LayerZero endpoint rather than the full `msg.value`. The difference `msg.value − Σ(estimatedFee_i)` accumulates in the contract permanently, as no ETH withdrawal, rescue, or fallback mechanism exists.

## Finding Description
In `updateRate()`, the loop calls `estimateFees()` per receiver and forwards only that exact amount to LayerZero:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [1](#0-0) 

The `payable(msg.sender)` refund address passed to LayerZero's `send()` only receives a refund if LayerZero itself holds surplus value. Since the contract forwards exactly `estimatedFee` — not `msg.value` — LayerZero has no surplus to return. Any `msg.value` beyond `Σ(estimatedFee_i)` remains in `MultiChainRateProvider`.

A grep and full read of the contract confirms there is no `receive()`, `fallback()`, `withdraw()`, or any ETH rescue function. [2](#0-1) 

This contrasts directly with `CrossChainRateProvider.updateRate()`, which correctly forwards the entire `msg.value` to LayerZero, allowing LayerZero's built-in refund mechanism to return any surplus to `msg.sender`:

```solidity
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [3](#0-2) 

## Impact Explanation
Any ETH sent above the exact sum of per-receiver LayerZero fees is permanently frozen inside `MultiChainRateProvider`. The contract has no recovery path. This is **Critical: Permanent freezing of funds** — the caller's ETH is irrecoverably locked in the contract on every overpaying call. [4](#0-3) 

## Likelihood Explanation
`updateRate()` has no access control and is callable by any external account. [4](#0-3) 
Callers must estimate the total fee off-chain by summing `estimateFees` across all receivers. Fee estimates are volatile (gas price fluctuations), the receiver list can change between estimation and execution via `addRateReceiver`/`removeRateReceiver`, and callers routinely add a buffer to prevent transaction failure — all of which produce excess ETH. The contract also exposes `estimateTotalFee()` as a convenience, but the estimate is stale by the time `updateRate()` executes. [5](#0-4) 

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

Alternatively, forward `msg.value` proportionally or in full to LayerZero and rely on its native refund mechanism, consistent with `CrossChainRateProvider`.

## Proof of Concept
1. Deploy `MultiChainRateProvider` with 3 configured rate receivers.
2. Call `estimateTotalFee()` → returns `0.024 ether` (3 × `0.008 ether`).
3. Call `updateRate{ value: 0.030 ether }()` (10% buffer, standard practice).
4. The loop forwards `0.008 ether` × 3 = `0.024 ether` to LayerZero.
5. `address(multiChainRateProvider).balance` is now `0.006 ether`.
6. No function exists to recover it; `0.006 ether` is permanently locked.
7. Repeat on every call with any buffer — loss accumulates indefinitely.

Foundry test sketch:
```solidity
function test_excessEthLocked() public {
    // Setup: 3 receivers configured
    uint256 totalFee = provider.estimateTotalFee();
    uint256 overpay = totalFee + 0.006 ether;

    vm.deal(alice, overpay);
    vm.prank(alice);
    provider.updateRate{ value: overpay }();

    assertEq(address(provider).balance, 0.006 ether); // locked
    assertEq(alice.balance, 0);                        // not refunded
}
```

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
