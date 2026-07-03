### Title
Unbounded Loop in `updateRate()` Can Permanently Block Cross-Chain Rate Distribution - (File: `contracts/cross-chain/MultiChainRateProvider.sol`)

---

### Summary

`MultiChainRateProvider.updateRate()` iterates over every entry in the `rateReceivers` array in a single transaction, making two external LayerZero calls per receiver. A single failing receiver causes the entire transaction to revert, blocking rate updates to all chains. As the receiver list grows, gas consumption grows without bound and can exceed the block gas limit.

---

### Finding Description

`updateRate()` is the sole mechanism for pushing the rsETH exchange rate from L1 to all registered L2 chains. It loops over `rateReceivers` without any pagination or partial-update capability:

```solidity
// contracts/cross-chain/MultiChainRateProvider.sol lines 108-137
function updateRate() external payable nonReentrant {
    ...
    uint256 rateReceiversLength = rateReceivers.length;

    for (uint256 i; i < rateReceiversLength;) {
        uint16 dstChainId = uint16(rateReceivers[i]._chainId);
        bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

        (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
            .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );
        unchecked { ++i; }
    }
    emit RateUpdated(rate);
}
```

Two failure modes exist:

**1. Single-receiver failure blocks all updates.** If any one receiver's `estimateFees` or `send` reverts (e.g., the LayerZero endpoint rejects the destination chain ID, the receiver contract address is misconfigured, or the accumulated ETH forwarded runs short mid-loop), the entire transaction reverts. No chain receives the updated rate. There is no `try/catch` and no partial-update path.

**2. Unbounded gas / ETH requirement.** Each iteration performs two external calls. As the protocol expands to more L2s (Arbitrum, Base, Optimism, Linea, Scroll, Unichain are already present in the codebase), both gas and the required `msg.value` grow linearly. With enough receivers the transaction will exceed the block gas limit, permanently bricking `updateRate()`.

There is no alternative entry point such as `updateRate(uint256 start, uint256 end)` or `updateRate(uint16[] memory chainIds)` that would allow partial updates.

---

### Impact Explanation

L2 deposit pools (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, etc.) rely on the rate pushed by this contract to price rsETH swaps. If `updateRate()` is permanently blocked, L2 pools operate on a stale exchange rate indefinitely. Users depositing ETH/LSTs on L2 receive rsETH amounts calculated from an outdated rate, causing either over-issuance (protocol insolvency) or under-issuance (theft of user yield). This maps to **Medium — Unbounded gas consumption** and **Medium — Permanent freezing of unclaimed yield** (stale rate = incorrect yield accrual for L2 depositors).

---

### Likelihood Explanation

The Kelp DAO protocol is actively expanding to new L2 networks. Each new chain adds one entry to `rateReceivers`. The `addRateReceiver` function is owner-controlled and has no cap. A misconfigured receiver (wrong chain ID, wrong contract address) added by the owner, or a LayerZero endpoint deprecation for one chain, is a realistic operational event that would trigger the all-or-nothing revert. The gas-limit scenario becomes realistic once the receiver count reaches ~15–20 chains.

---

### Recommendation

1. Add paginated and selective update functions:
   ```solidity
   function updateRate(uint256 start, uint256 finish) external payable nonReentrant;
   function updateRate(uint256[] calldata indices) external payable nonReentrant;
   ```
2. Wrap each individual `send` call in a `try/catch` so a single failing receiver does not block the rest.
3. Enforce a maximum cap on `rateReceivers.length` in `addRateReceiver`.

---

### Proof of Concept

1. Owner calls `addRateReceiver` to register chains A, B, C, D (four receivers).
2. Chain C's LayerZero endpoint is deprecated or the receiver contract address is wrong.
3. Caller sends sufficient ETH and calls `updateRate()`.
4. The loop succeeds for A and B, then reverts on C's `send` call.
5. The entire transaction reverts — chains A, B, and D also receive no update.
6. All L2 pools now operate on a stale rsETH rate indefinitely until the broken receiver is removed and the owner calls `removeRateReceiver`, which itself requires knowing the correct index.

Relevant code: [1](#0-0) 

`addRateReceiver` has no cap and no validation of the destination contract: [2](#0-1)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L72-76)
```text
    function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
        rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));

        emit RateReceiverAdded(_chainId, _contract);
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
