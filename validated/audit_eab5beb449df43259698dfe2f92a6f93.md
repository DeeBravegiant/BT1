Audit Report

## Title
Excess Native ETH Sent to `MultiChainRateProvider.updateRate()` Is Permanently Trapped in the Contract - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` is a permissionless `payable` function that forwards exactly `estimatedFee` per receiver to the LayerZero endpoint, but never validates that `msg.value` equals the total required fee and never refunds any remainder. The contract has no `withdraw` or sweep function, so any ETH sent beyond the sum of per-chain fees is permanently locked.

## Finding Description
In `updateRate()` (L108–137), each iteration calls `estimateFees()` and forwards exactly that amount to the LZ endpoint:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
```

Because only `estimatedFee` (not `msg.value`) is forwarded to the LZ endpoint per iteration, the `payable(msg.sender)` refund address inside the LZ `send` call can only return excess from what was passed to LZ — it cannot return ETH that was never forwarded. Any `msg.value` above the sum of all `estimatedFee` values remains in `MultiChainRateProvider` itself.

The contract inherits only `Ownable` and `ReentrancyGuard` and defines no `withdraw`, `rescue`, or `receive`-with-sweep function, making the trapped ETH permanently unrecoverable.

By contrast, `CrossChainRateProvider.updateRate()` (L96–98) passes the full `msg.value` to LZ with `{ value: msg.value }` and supplies `payable(msg.sender)` as the refund address, so LZ itself returns any excess — that contract is not affected.

## Impact Explanation
**Low — Contract fails to deliver promised returns.** Any ETH sent above the sum of per-chain `estimatedFee` values is permanently frozen inside `MultiChainRateProvider`. The contract exposes `estimateTotalFee()` as a view helper, but on-chain fee estimates can shift between the view call and the actual transaction (e.g., due to gas price changes or LZ config updates). A caller who adds a small buffer to avoid a mid-loop revert will permanently lose that buffer with no recovery path.

## Likelihood Explanation
`updateRate()` carries no access control — any external account or keeper bot can call it. Callers who consult `estimateTotalFee()` off-chain and submit a transaction with a small ETH buffer (a standard defensive pattern when fees are volatile) will silently lose the buffer. Likelihood is **Low-to-Medium** given the permissionless entry point and the common practice of adding a fee buffer.

## Recommendation
After the loop, compute the total consumed fee and refund any remainder to `msg.sender`:

```solidity
uint256 totalConsumed;
for (uint256 i; i < rateReceiversLength;) {
    // ... existing fee estimation and send logic ...
    totalConsumed += estimatedFee;
    unchecked { ++i; }
}
uint256 excess = msg.value - totalConsumed;
if (excess > 0) {
    (bool ok,) = payable(msg.sender).call{ value: excess }("");
    require(ok, "Refund failed");
}
```

Alternatively, add a pre-loop check `require(msg.value >= estimateTotalFee(), "Insufficient fee")` and refund the difference after the loop.

## Proof of Concept
1. Deploy `MultiChainRateProvider` with two configured `rateReceivers`.
2. Call `estimateTotalFee()` — suppose it returns `0.01 ETH`.
3. Call `updateRate{ value: 0.02 ETH }()` (caller adds a 2× buffer).
4. The loop consumes exactly `0.01 ETH` across the two LZ sends (each `send` receives only `estimatedFee`).
5. The remaining `0.01 ETH` stays in the contract's balance.
6. No function exists to recover it; the ETH is permanently frozen.