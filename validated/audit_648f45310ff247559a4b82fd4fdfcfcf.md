Audit Report

## Title
Excess `msg.value` Permanently Trapped in `MultiChainRateProvider.updateRate()` With No Refund or Recovery Path - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`updateRate()` is a public `payable` function that forwards exactly the on-chain `estimatedFee` to the LayerZero endpoint for each registered receiver. Any `msg.value` exceeding the sum of those per-receiver fees remains in the contract permanently, as neither `MultiChainRateProvider` nor its concrete implementations (`RSETHMultiChainRateProvider`, `AGETHMultiChainRateProvider`) contain any ETH withdrawal or recovery function. The `Recoverable` utility contract exists in the codebase but is not inherited here.

## Finding Description
In `contracts/cross-chain/MultiChainRateProvider.sol` lines 108–137, `updateRate()` loops over all `rateReceivers`, calls `ILayerZeroEndpoint.estimateFees()` on-chain for each, and immediately forwards exactly that `estimatedFee` to the endpoint:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
```

After the loop completes, there is no logic to compute `address(this).balance` and refund the remainder to `msg.sender`. The contract inherits only `Ownable` and `ReentrancyGuard`; neither adds an ETH rescue path. `contracts/utils/Recoverable.sol` provides `recoverETH()` but is not inherited by `MultiChainRateProvider`, `RSETHMultiChainRateProvider`, or `AGETHMultiChainRateProvider`.

Because `estimateTotalFee()` is a view function called off-chain before the transaction is submitted, the fee estimate is stale by the time the transaction is mined (gas price fluctuations, network congestion). Callers must add a buffer to avoid a mid-loop revert. That buffer — and any other overpayment — is permanently locked in the contract with no admin or user rescue path.

## Impact Explanation
Every call to `updateRate()` that includes even 1 wei of overpayment results in permanently frozen ETH. The contract has no `receive()`-plus-withdrawal pattern, no `recoverETH()`, and no `fallback()` that could be used to drain it. Over repeated calls (the protocol pushes rates to 10+ chains regularly), the trapped balance accumulates irreversibly. This matches **Critical — Permanent freezing of funds**.

## Likelihood Explanation
`updateRate()` carries no access-control modifier; any external account can call it. The protocol itself calls this function regularly to push rates across many chains. Because `estimateTotalFee()` is a view call that is stale at mine time, and because callers must over-pay to avoid mid-loop reverts, overpayment is a near-certainty in normal operation. No attacker capability is required; the loss occurs through ordinary, expected usage.

## Recommendation
After the loop, refund any remaining balance to `msg.sender`:

```solidity
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool ok,) = payable(msg.sender).call{ value: remaining }("");
    require(ok, "refund failed");
}
```

Alternatively, inherit `contracts/utils/Recoverable.sol` in `MultiChainRateProvider` to allow an admin to rescue trapped ETH via the existing `recoverETH()` function.

## Proof of Concept
1. Deploy `RSETHMultiChainRateProvider` with 10 `rateReceivers`.
2. Call `estimateTotalFee()` off-chain → returns `X` wei.
3. Call `updateRate{ value: X + 1e15 }()` (0.001 ETH buffer).
4. The loop sends exactly `estimatedFee_i` for each of the 10 receivers, consuming `X` wei total.
5. `address(contract).balance` is now `1e15` wei.
6. No function exists to withdraw it. Call every public/external function on the contract — none transfers ETH out. The 0.001 ETH is permanently frozen.
7. Repeat across many rate-update cycles; the trapped balance grows monotonically.