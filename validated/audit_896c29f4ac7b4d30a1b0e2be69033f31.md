Audit Report

## Title
Excess ETH Sent to `updateRate()` Is Permanently Trapped With No Refund or Recovery - (File: contracts/cross-chain/MultiChainRateProvider.sol)

## Summary
`MultiChainRateProvider.updateRate()` forwards only the on-chain `estimatedFee` per receiver to LayerZero, leaving any `msg.value` surplus in the contract. The contract inherits only `Ownable` and `ReentrancyGuard`, has no `receive()`/`fallback()`, and no owner ETH sweep function, so the surplus is permanently unrecoverable.

## Finding Description
In `updateRate()` (lines 108–137), for each registered receiver the function queries `estimateFees()` and forwards exactly that amount to LayerZero:

```solidity
// MultiChainRateProvider.sol L124-129
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
```

The `payable(msg.sender)` argument is LayerZero's internal refund address — it only covers excess ETH that LayerZero itself receives above its own cost. ETH that is never forwarded to LayerZero (i.e., `msg.value − Σ estimatedFee[i]`) never reaches LayerZero and therefore is never refunded. It remains in `MultiChainRateProvider`.

The contract has no mechanism to recover this ETH:
- Inherits only `Ownable` and `ReentrancyGuard` (lines 4–5, 13) — neither provides ETH withdrawal.
- No `receive()` or `fallback()` function.
- No owner-only sweep or rescue function anywhere in the contract.

Contrast with `CrossChainRateProvider.updateRate()` (line 96), which forwards the full `msg.value` to LayerZero and relies on LayerZero's refund mechanism to return any excess to `msg.sender`. `MultiChainRateProvider` does not use this pattern.

## Impact Explanation
Any ETH sent beyond `Σ estimatedFee[i]` is permanently frozen in the contract with zero recovery path. This directly matches the **Critical — Permanent freezing of funds** impact class. The frozen ETH belongs to the caller and is irrecoverable by any party.

## Likelihood Explanation
`updateRate()` has no access control — any external account can call it. Callers must estimate total fees off-chain before calling; because `estimateFees()` returns a point-in-time value that can shift between estimation and execution (gas price changes, base fee changes), callers routinely add a safety buffer. That buffer is the ETH that becomes permanently trapped. With multiple receivers registered, the per-leg estimation variance compounds, increasing the likely buffer size and the magnitude of trapped funds. The scenario is a normal, expected usage pattern, not an edge case.

## Recommendation
After the loop, refund any unspent ETH to the caller:

```solidity
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool ok,) = payable(msg.sender).call{value: remaining}("");
    require(ok, "refund failed");
}
```

Alternatively, track total spent explicitly (`totalSpent += estimatedFee`) and refund `msg.value - totalSpent`. At minimum, add an owner-only ETH recovery function.

## Proof of Concept
1. Deploy `MultiChainRateProvider` with two registered receivers.
2. Call `estimateTotalFee()` off-chain → returns `0.002 ETH`.
3. Caller sends `0.003 ETH` to `updateRate()` as a buffer.
4. Inside the loop, both `estimateFees()` calls return `0.001 ETH` each at execution time.
5. Two `send{ value: 0.001 ETH }` calls consume `0.002 ETH` total.
6. `address(MultiChainRateProvider).balance == 0.001 ETH` after the call.
7. No function exists to withdraw it; call any view or write function — none can move the ETH. It is permanently frozen.

Foundry fork test plan: fork mainnet/Ethereum, deploy a concrete subclass of `MultiChainRateProvider`, register two receivers, call `updateRate{value: estimateTotalFee() + 1e15}()`, assert `address(provider).balance == 1e15` and that no subsequent call reduces it.