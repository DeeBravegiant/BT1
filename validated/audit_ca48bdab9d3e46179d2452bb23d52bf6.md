### Title
Excess ETH sent to `MultiChainRateProvider.updateRate()` is permanently stuck in the contract - (File: contracts/cross-chain/MultiChainRateProvider.sol)

### Summary
`MultiChainRateProvider.updateRate()` is `payable` and accepts ETH for LayerZero cross-chain fees, but only forwards the on-chain-estimated fee amount per receiver — not `msg.value`. Any ETH sent beyond the sum of estimated fees is permanently locked in the contract, which has no recovery mechanism.

### Finding Description
`updateRate()` is a public, `payable`, `nonReentrant` function. For each entry in `rateReceivers`, it calls `estimateFees()` on-chain to compute `estimatedFee`, then sends exactly that amount to the LayerZero endpoint:

```solidity
(uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
    .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(...);
```

The total ETH consumed is `Σ estimatedFee_i`. If `msg.value > Σ estimatedFee_i`, the difference is silently retained by the contract. `MultiChainRateProvider` has no `receive()` function, no ETH withdrawal function, and no owner-callable recovery path. The contract inherits only from `Ownable` and `ReentrancyGuard`, neither of which provides ETH recovery. [1](#0-0) 

Callers routinely overpay LayerZero fees to guarantee delivery, since the on-chain estimate can differ from the actual fee at execution time. The documentation for `updateRate()` explicitly instructs callers to consult off-chain fee estimation guides, implying that exact fee matching is not enforced. [2](#0-1) 

### Impact Explanation
Any ETH overpaid to `updateRate()` is permanently frozen in the `MultiChainRateProvider` contract. There is no owner withdrawal, no `receive()` refund path, and no sweep function. This constitutes permanent freezing of user funds.

Impact: **Medium — Permanent freezing of unclaimed yield / user ETH** (the frozen ETH is the caller's own overpayment, not protocol TVL, but it is irrecoverable).

### Likelihood Explanation
`updateRate()` has no access control — any external account can call it. Callers must supply ETH for LayerZero fees and commonly overpay to avoid reverts. The number of `rateReceivers` can vary, making exact fee pre-computation difficult off-chain. Every call that overpays by even 1 wei results in permanently stuck ETH.

### Recommendation
After the loop, refund any remaining ETH to `msg.sender`:

```solidity
uint256 remaining = address(this).balance;
if (remaining > 0) {
    (bool ok,) = payable(msg.sender).call{value: remaining}("");
    require(ok, "ETH refund failed");
}
```

Alternatively, add an owner-callable `recoverETH()` function, or compute the total estimated fee before the loop and revert if `msg.value` does not match exactly.

### Proof of Concept
1. Suppose there are 3 rate receivers, each with `estimatedFee = 0.01 ETH` → total needed = `0.03 ETH`.
2. Caller calls `updateRate{value: 0.1 ETH}()` to ensure the transaction succeeds.
3. The loop sends `0.01 ETH` × 3 = `0.03 ETH` to LayerZero endpoints.
4. The remaining `0.07 ETH` stays in `MultiChainRateProvider`.
5. No function exists to withdraw it — it is permanently frozen. [3](#0-2)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L105-134)
```text
    /// @dev This function is set to payable to pay for gas on execute lzReceive (on the receiver contract)
    /// on the destination chain. To compute the correct value to send check here -
    /// https://layerzero.gitbook.io/docs/evm-guides/code-examples/estimating-message-fees
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
```
