Audit Report

## Title
Excess ETH sent to `updateRate()` is permanently locked with no recovery path - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` is `payable` and accepts ETH for LayerZero cross-chain fees, but only forwards the on-chain-estimated fee per receiver to the LayerZero endpoint. Any ETH in `msg.value` exceeding the sum of per-receiver `estimatedFee` values is retained by the contract permanently, as the contract has no `receive()` fallback, no withdrawal function, and no owner-callable ETH recovery path.

## Finding Description
`updateRate()` (line 108) is `external payable nonReentrant`. Inside the loop (lines 119–134), for each entry in `rateReceivers` it calls `ILayerZeroEndpoint.estimateFees()` to obtain `estimatedFee`, then calls `ILayerZeroEndpoint.send{ value: estimatedFee }(...)` forwarding exactly that amount. The `_refundAddress` argument passed to `send()` is `payable(msg.sender)` (line 128), which only handles refunds from the LayerZero endpoint itself if the endpoint receives more than it consumes — it does not refund ETH that was never forwarded to the endpoint in the first place.

After the loop completes, any ETH equal to `msg.value − Σ estimatedFee_i` remains in the `MultiChainRateProvider` contract. The contract:
- Has no `receive()` or `fallback()` function.
- Inherits only `Ownable` and `ReentrancyGuard`, neither of which provides ETH recovery.
- Has no `withdraw`, `recoverETH`, or sweep function.

The contract also exposes `estimateTotalFee()` (line 154), but this is a view function that computes fees at the time of the call; the actual fees consumed during `updateRate()` are re-estimated inside the loop at execution time and may differ. Callers who overpay to guarantee delivery (a standard practice with LayerZero) will permanently lose the excess.

## Impact Explanation
Any ETH overpaid to `updateRate()` is permanently frozen in the contract with no recovery mechanism. This matches **Critical — Permanent freezing of funds**: the caller's ETH is irrecoverably locked in the contract, not merely delayed or at risk.

## Likelihood Explanation
`updateRate()` has no access control — any external account can call it. LayerZero fee estimates are known to fluctuate, and callers routinely overpay to avoid reverts. The contract itself documents this pattern by linking to off-chain fee estimation guides (lines 105–107). Every call where `msg.value > Σ estimatedFee_i` results in permanently stuck ETH. The condition is trivially reachable by any caller on any invocation.

## Recommendation
After the loop, refund any remaining contract balance to `msg.sender`:

```solidity
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool ok,) = payable(msg.sender).call{value: remaining}("");
    require(ok, "ETH refund failed");
}
```

Alternatively, compute `estimateTotalFee()` before the loop, require `msg.value == totalFee`, and revert on mismatch to enforce exact payment.

## Proof of Concept
1. Deploy `MultiChainRateProvider` with 3 rate receivers, each with `estimatedFee = 0.01 ETH` at execution time.
2. Call `updateRate{value: 0.1 ETH}()` from any EOA.
3. The loop sends `0.01 ETH × 3 = 0.03 ETH` to the LayerZero endpoint.
4. `address(MultiChainRateProvider).balance` is now `0.07 ETH`.
5. No function exists to withdraw it. Call any function on the contract — none can move the ETH. The `0.07 ETH` is permanently frozen.

Foundry fork test plan: fork mainnet/testnet with a live LayerZero endpoint, deploy a concrete subclass of `MultiChainRateProvider`, add receivers, call `updateRate{value: X}()` with `X > estimateTotalFee()`, and assert `address(provider).balance > 0` after the call with no callable path to reduce it.