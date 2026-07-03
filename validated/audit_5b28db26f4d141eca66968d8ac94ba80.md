### Title
Excess `msg.value` Permanently Locked in `MultiChainRateProvider.updateRate()` Due to No Refund Mechanism After Iterative Fee Distribution - (File: contracts/cross-chain/MultiChainRateProvider.sol)

---

### Summary

`MultiChainRateProvider.updateRate()` is a public `payable` function that iterates over all registered `rateReceivers` and sends a freshly-estimated LayerZero fee for each one. There is no validation that `msg.value` equals the sum of all per-receiver fees, and the contract has no `withdraw`, `rescue`, or `receive`-based recovery path. Any ETH sent beyond the sum of individual `estimatedFee` values is permanently locked in the contract.

---

### Finding Description

`updateRate()` is callable by any external account with no access control:

```solidity
function updateRate() external payable nonReentrant {
```

Inside the loop, each iteration independently estimates and spends a fee:

```solidity
for (uint256 i; i < rateReceiversLength;) {
    (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
        .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

    ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
        dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
    );
    ...
}
```

The total ETH consumed is `Σ estimatedFee[i]`. The function never checks that `msg.value == Σ estimatedFee[i]`. Any ETH in excess of that sum remains in the contract's balance after the call. Neither `MultiChainRateProvider` nor its concrete implementation `RSETHMultiChainRateProvider` defines any `withdraw`, `sweep`, `rescue`, or `receive` function. A search across the entire `contracts/cross-chain/` directory confirms no such recovery path exists.

This is the direct analog of the reported bug: `msg.value` is the source of funds for multiple iterative sends, but the contract does not enforce that the caller's payment exactly matches the total cost, and provides no way to reclaim the difference.

---

### Impact Explanation

**Impact: Critical — Permanent freezing of caller funds.**

Any ETH sent above `Σ estimatedFee[i]` is irrecoverably locked in `RSETHMultiChainRateProvider` (or any other `MultiChainRateProvider` deployment). There is no owner-callable sweep, no `receive()` fallback that routes funds out, and no upgrade path that could rescue them. The locked ETH is not yield — it is principal permanently removed from the caller's control.

---

### Likelihood Explanation

**Likelihood: High.**

- `updateRate()` has no access control; any account can call it.
- Callers are directed to use `estimateTotalFee()` to compute the required payment, but that view function reads fees at a different block than the actual execution. LayerZero fee estimates are volatile (they depend on destination gas prices and oracle state), so the value returned by `estimateTotalFee()` at block N will routinely differ from `Σ estimatedFee[i]` computed inside `updateRate()` at block N+k.
- Callers who defensively overpay to avoid a revert will permanently lose the excess.
- The discrepancy grows with the number of registered `rateReceivers`.

---

### Recommendation

Add an explicit check and refund:

```solidity
function updateRate() external payable nonReentrant {
    uint256 latestRate = getLatestRate();
    rate = latestRate;
    lastUpdated = block.timestamp;

    bytes memory _payload = abi.encode(latestRate);
    uint256 rateReceiversLength = rateReceivers.length;
    uint256 totalSpent;

    for (uint256 i; i < rateReceiversLength;) {
        uint16 dstChainId = uint16(rateReceivers[i]._chainId);
        bytes memory remoteAndLocalAddresses =
            abi.encodePacked(rateReceivers[i]._contract, address(this));

        (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
            .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );

        totalSpent += estimatedFee;
        unchecked { ++i; }
    }

    // Refund excess ETH to caller
    if (msg.value > totalSpent) {
        (bool ok,) = payable(msg.sender).call{ value: msg.value - totalSpent }("");
        require(ok, "Refund failed");
    }

    emit RateUpdated(rate);
}
```

---

### Proof of Concept

1. `RSETHMultiChainRateProvider` is deployed with two `rateReceivers` (e.g., Arbitrum and Optimism).
2. A caller queries `estimateTotalFee()` at block N and receives `1.0 ETH`.
3. The caller calls `updateRate{ value: 1.05 ETH }()` at block N+5 (intentionally overpaying to avoid revert risk).
4. Inside the loop, `estimatedFee` for each receiver is computed fresh. Suppose the sum is `0.98 ETH` (fees dropped slightly).
5. LayerZero `send` is called twice, consuming `0.98 ETH` total.
6. The remaining `0.07 ETH` stays in `RSETHMultiChainRateProvider`'s balance.
7. No function in `MultiChainRateProvider` or `RSETHMultiChainRateProvider` can move this ETH out. It is permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L9-34)
```text
contract RSETHMultiChainRateProvider is MultiChainRateProvider {
    address public immutable rsETHPriceOracle;

    constructor(address _rsETHPriceOracle, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;

        rateInfo = RateInfo({
            tokenSymbol: "rsETH",
            tokenAddress: 0xA1290d69c65A6Fe4DF752f95823fae25cB99e5A7, // rsETH token address on ETH mainnet
            baseTokenSymbol: "ETH",
            baseTokenAddress: address(0) // Address 0 for native tokens
        });

        layerZeroEndpoint = _layerZeroEndpoint;
    }

    /// @notice Returns the latest rate from the rsETH contract
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }

    /// @notice Calls the getLatestRate function and returns the rate
    function getRate() external view returns (uint256) {
        return getLatestRate();
    }
}
```
