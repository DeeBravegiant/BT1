Audit Report

## Title
Excess ETH sent to `updateRate()` is permanently locked with no recovery path - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` is `payable` and accepts ETH for LayerZero cross-chain fees, but only forwards the on-chain-estimated fee per receiver. Any ETH exceeding the sum of per-receiver `estimatedFee` values is silently retained by the contract. The contract contains no `receive()` fallback, no withdrawal function, and no owner-callable ETH recovery path, making any overpayment permanently irrecoverable.

## Finding Description
`updateRate()` (L108) is a public, `payable`, `nonReentrant` function. Inside the loop (L119–134), for each entry in `rateReceivers`, it calls `ILayerZeroEndpoint.estimateFees()` to obtain `estimatedFee` (L124–125), then calls `ILayerZeroEndpoint.send{ value: estimatedFee }(...)` (L127–129), forwarding exactly that amount. The `_refundAddress` parameter of `send()` is set to `payable(msg.sender)` (L128), which causes LayerZero to refund any unused portion of `estimatedFee` back to the caller — but this only covers the delta between `estimatedFee` and the actual fee charged by LayerZero. It does **not** cover the delta between `msg.value` and `Σ estimatedFee_i`.

After the loop, no refund of `msg.value - Σ estimatedFee_i` is issued. The contract inherits only `Ownable` and `ReentrancyGuard` (L4–5, L13); neither provides ETH recovery. The full contract (L1–182) contains no `receive()`, no `fallback()`, no `withdraw()`, and no sweep function. Any ETH left in the contract after the loop is permanently frozen.

## Impact Explanation
The caller's ETH overpayment is permanently frozen in the contract with no recovery path. This matches **Critical — Permanent freezing of funds** from the allowed impact scope. The amount is bounded only by how much the caller overpays; since callers routinely pad LayerZero fees to avoid reverts, and since `estimateTotalFee()` (L154) returns a view-time estimate that may differ from the estimate computed inside the transaction, overpayment is a normal operational condition, not an edge case.

## Likelihood Explanation
`updateRate()` has no access control — any external account can call it. Callers must supply ETH and commonly overpay to guarantee delivery. The contract itself documents (L105–107) that callers should consult off-chain fee estimation guides, implying exact matching is not enforced. Every call where `msg.value > Σ estimatedFee_i` results in permanently stuck ETH. The condition is trivially reachable by any unprivileged caller.

## Recommendation
After the loop, refund any remaining contract balance to `msg.sender`:

```solidity
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool ok,) = payable(msg.sender).call{value: remaining}("");
    require(ok, "ETH refund failed");
}
```

Alternatively, compute `totalEstimatedFee` before the loop (mirroring `estimateTotalFee()`) and `require(msg.value == totalEstimatedFee, "incorrect ETH")`, or add an owner-callable `recoverETH()` function.

## Proof of Concept
1. Deploy `MultiChainRateProvider` with 3 rate receivers, each with `estimatedFee = 0.01 ETH` at call time.
2. Call `updateRate{value: 0.1 ETH}()`.
3. The loop sends `0.01 ETH × 3 = 0.03 ETH` to the LayerZero endpoint; LayerZero refunds any per-send surplus to `msg.sender` via the `_refundAddress` parameter.
4. `0.1 ETH − 0.03 ETH = 0.07 ETH` remains in `MultiChainRateProvider`.
5. Inspect `address(multiChainRateProvider).balance` — it equals `0.07 ETH`.
6. Attempt any withdrawal — no function exists. The ETH is permanently frozen.

Foundry fork test plan: fork mainnet/testnet, mock `ILayerZeroEndpoint` to return fixed `estimatedFee` values and consume exactly that amount on `send()`, call `updateRate{value: 10x estimatedFee}()`, assert `address(provider).balance > 0` after the call, and assert no function can reduce it.