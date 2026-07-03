### Title
Excess ETH Permanently Stuck in `MultiChainRateProvider::updateRate()` With No Recovery Path - (File: contracts/cross-chain/MultiChainRateProvider.sol)

### Summary
`MultiChainRateProvider::updateRate()` is a `payable` function that accepts ETH to cover LayerZero messaging fees. It forwards exactly `estimatedFee` per receiver to the LayerZero endpoint, but any ETH sent beyond the sum of all estimated fees has no refund path back to the caller and no withdrawal mechanism in the contract, permanently locking the excess.

### Finding Description
`updateRate()` iterates over all `rateReceivers` and for each one calls `ILayerZeroEndpoint.estimateFees()` on-chain to obtain the exact fee, then forwards only that amount to LayerZero:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
```

The `payable(msg.sender)` argument is LayerZero's internal per-send refund address (used if LayerZero itself consumes less than `estimatedFee`). It does **not** refund the difference between `msg.value` and the total fees consumed across all sends.

If `msg.value > Σ estimatedFee_i`, the surplus ETH remains in the `MultiChainRateProvider` contract. The contract:
- Has no `receive()` or `fallback()` function
- Has no `withdraw` or rescue function
- Is not upgradeable (inherits non-upgradeable `Ownable`)

There is no on-chain path to recover the stranded ETH.

### Impact Explanation
Any ETH sent in excess of the exact total LayerZero fee is permanently frozen in the contract. Because the contract is non-upgradeable and has no withdrawal function, the funds cannot be recovered by any party. This satisfies the **Critical – Permanent freezing of funds** impact category.

### Likelihood Explanation
Callers (including the protocol's own keeper/operator) routinely add a safety buffer when paying for cross-chain messaging fees to guard against fee fluctuations between the off-chain estimate and the on-chain execution. The `estimateTotalFee()` view function is provided precisely for pre-flight estimation, but gas-price volatility means callers often overpay. Any overpayment is irrecoverable. Likelihood is **Medium** (common operational pattern, not a one-off mistake).

### Recommendation
Add an explicit refund of unused ETH at the end of `updateRate()`:

```solidity
function updateRate() external payable nonReentrant {
    // ... existing loop ...

    // Refund any unused ETH to the caller
    uint256 remaining = address(this).balance;
    if (remaining > 0) {
        (bool ok,) = payable(msg.sender).call{value: remaining}("");
        require(ok, "refund failed");
    }
}
```

Alternatively, add an owner-only ETH rescue function, or enforce `msg.value == estimateTotalFee()` with a strict equality check.

### Proof of Concept
1. Deploy `MultiChainRateProvider` with two rate receivers.
2. Call `estimateTotalFee()` → returns `0.01 ETH`.
3. Call `updateRate{value: 0.1 ETH}()` (caller adds a 10× buffer).
4. Inside the loop, `estimatedFee` for each receiver is computed on-chain; only `0.01 ETH` total is forwarded to LayerZero.
5. `0.09 ETH` remains in the contract.
6. No function exists to withdraw it; the ETH is permanently locked. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L62-66)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }
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
