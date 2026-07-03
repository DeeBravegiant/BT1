Audit Report

## Title
Excess ETH Permanently Locked in `updateRate()` Due to Missing Post-Loop Refund - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` is a public payable function that forwards exactly the on-chain `estimatedFee` to each LayerZero `send()` call per receiver, but never returns any remaining `msg.value` to the caller after the loop. The contract contains no ETH recovery mechanism, so any ETH sent beyond the exact sum of per-receiver estimated fees is permanently locked in the contract.

## Finding Description
In `MultiChainRateProvider.updateRate()` (L108–137), the loop queries `estimateFees()` for each receiver and forwards exactly that amount:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [1](#0-0) 

The `payable(msg.sender)` argument is LayerZero's internal refund address for its own excess destination-chain gas — it does not cause LayerZero to return unspent source-chain `msg.value` to the caller. After the loop completes at L134, no refund of `msg.value − Σ(estimatedFee_i)` is issued. [2](#0-1) 

The contract inherits only `Ownable` and `ReentrancyGuard`, has no `receive()` or `fallback()` function, and no owner-callable ETH sweep or rescue function anywhere in the file. [3](#0-2) 

This contrasts directly with `CrossChainRateProvider.updateRate()` (L96), which forwards the full `msg.value` to the single LayerZero call and lets LayerZero refund the excess:

```solidity
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(...)
``` [4](#0-3) 

That pattern is correct for a single receiver but cannot be applied directly to the multi-receiver case, which is why the surplus goes unrefunded.

## Impact Explanation
Any ETH sent above the exact sum of per-receiver estimated fees is permanently frozen in the `MultiChainRateProvider` contract. There is no owner-callable sweep, no `receive()` drain path, and no upgrade mechanism. This constitutes **permanent freezing of caller funds**, matching the Critical impact category.

## Likelihood Explanation
`updateRate()` carries no access control — any external account may call it. [5](#0-4)  LayerZero fees fluctuate with gas prices and oracle state. The contract itself exposes `estimateTotalFee()` as a helper, and callers who use it off-chain and add a safety buffer (standard practice to avoid reverts) will routinely send more ETH than consumed. The surplus accumulates across all callers over time.

## Recommendation
After the loop, refund any unspent ETH to `msg.sender`:

```solidity
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool ok,) = payable(msg.sender).call{value: remaining}("");
    require(ok, "refund failed");
}
```

Alternatively, pre-compute the total fee before the loop and require `msg.value` to equal it exactly, reverting if it does not match.

## Proof of Concept
1. Two rate receivers are configured; `estimateTotalFee()` returns `0.01 ETH`.
2. Caller invokes `updateRate{value: 0.02 ETH}()` — a common pattern to avoid reverts from fee fluctuation.
3. The loop sends `estimatedFee_0` and `estimatedFee_1` (summing to `0.01 ETH`) to LayerZero. [6](#0-5) 
4. After the loop, `address(this).balance == 0.01 ETH`. No refund is issued.
5. The `0.01 ETH` surplus is permanently locked; the contract has no function to recover it. [7](#0-6) 

**Foundry test plan**: Deploy a mock `ILayerZeroEndpoint` that returns a fixed `estimatedFee` and records `send()` calls. Deploy a concrete subclass of `MultiChainRateProvider` with two receivers. Call `updateRate{value: 2 * estimatedFee + 1 wei}()`. Assert `address(provider).balance == 1 wei` after the call and that no refund event or transfer to `msg.sender` occurred.

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L13-13)
```text
abstract contract MultiChainRateProvider is Ownable, ReentrancyGuard {
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-108)
```text
    function updateRate() external payable nonReentrant {
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L119-134)
```text
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
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L136-137)
```text
        emit RateUpdated(rate);
    }
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L96-98)
```text
        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );
```
