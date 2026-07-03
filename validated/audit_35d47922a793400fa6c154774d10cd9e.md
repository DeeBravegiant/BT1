### Title
Excess ETH Not Refunded to Caller in `updateRate()` — (File: contracts/cross-chain/MultiChainRateProvider.sol)

---

### Summary

`MultiChainRateProvider.updateRate()` is a public `payable` function that accepts ETH from any caller to cover LayerZero cross-chain messaging fees. It iterates over all configured rate receivers and forwards exactly `estimatedFee` per destination chain to the LayerZero endpoint. Any ETH sent by the caller beyond the sum of those estimated fees is permanently trapped in the contract, as there is no refund logic and no recovery mechanism.

---

### Finding Description

`MultiChainRateProvider.updateRate()` is callable by any external account with no access restriction:

```solidity
function updateRate() external payable nonReentrant {
    ...
    for (uint256 i; i < rateReceiversLength;) {
        ...
        (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
            .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );
        ...
    }
}
```

The function consumes exactly `estimatedFee` per chain from the contract's balance (funded by `msg.value`). The refund address `payable(msg.sender)` passed to LayerZero's `send` only covers any excess within each individual LayerZero call — it does **not** refund the difference between `msg.value` and `sum(estimatedFees)` back to the caller. That remainder stays in the `MultiChainRateProvider` contract.

The contract inherits only `Ownable` and `ReentrancyGuard` — there is no sweep, rescue, or recovery function to retrieve stranded ETH. [1](#0-0) 

Contrast this with `CrossChainRateProvider.updateRate()`, which forwards the entire `msg.value` directly to LayerZero (`send{ value: msg.value }`), so LayerZero's own refund mechanism handles any excess. `MultiChainRateProvider` does not do this — it only forwards `estimatedFee` per iteration. [2](#0-1) 

---

### Impact Explanation

Any ETH sent in excess of `sum(estimatedFees)` is permanently frozen in `MultiChainRateProvider`. Because the contract has no ETH recovery function and is not `Recoverable`, the funds cannot be retrieved by the owner or any other party. This constitutes permanent freezing of caller funds.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

`updateRate()` has no access control — any external account can call it. Callers are expected to estimate the required ETH using `estimateTotalFee()`, but:

- Callers commonly add a safety buffer to avoid reverts due to fee fluctuations.
- The `estimatedFee` from LayerZero is itself an estimate and may differ from the actual fee consumed.
- Any buffer or rounding excess is silently trapped.

**Likelihood: Low** — requires the caller to overpay, but this is a common and expected pattern when paying for cross-chain gas.

---

### Recommendation

After the loop, refund any unused ETH to the caller:

```solidity
function updateRate() external payable nonReentrant {
    ...
    // existing loop
    ...
    uint256 remaining = address(this).balance;
    if (remaining > 0) {
        (bool success, ) = payable(msg.sender).call{value: remaining}("");
        require(success, "Refund failed");
    }
}
```

Alternatively, compute `estimateTotalFee()` on-chain at the start of `updateRate()`, verify `msg.value >= totalFee`, and revert if underpaid, then refund the difference after the loop.

---

### Proof of Concept

1. Deploy `MultiChainRateProvider` with two rate receivers on different chains.
2. Call `estimateTotalFee()` — suppose it returns `0.01 ETH`.
3. Call `updateRate()` with `msg.value = 0.02 ETH` (a 2× buffer, common practice).
4. The loop sends `estimatedFee` (≈ `0.005 ETH`) to LayerZero for each of the two chains, consuming `≈ 0.01 ETH` total.
5. The remaining `≈ 0.01 ETH` stays in the `MultiChainRateProvider` contract.
6. No function exists to recover it — the ETH is permanently frozen. [3](#0-2)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-136)
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
```

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
