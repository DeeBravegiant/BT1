### Title
Sequential Unbounded External Calls in `updateRate()` Can Permanently Block Cross-Chain rsETH Rate Broadcasts - (File: `contracts/cross-chain/MultiChainRateProvider.sol`)

### Summary
`MultiChainRateProvider.updateRate()` iterates sequentially over an unbounded `rateReceivers` array, making two external LayerZero calls per receiver with no cap on array length. As the protocol expands to more destination chains, gas consumption grows linearly until the function exceeds the block gas limit and becomes permanently uncallable, freezing rsETH rate updates to all destination chains.

### Finding Description
`updateRate()` in `contracts/cross-chain/MultiChainRateProvider.sol` is the sole mechanism for broadcasting the rsETH/ETH exchange rate to all registered cross-chain receivers. The function iterates over every entry in `rateReceivers` and, for each one, makes two sequential external calls to the LayerZero endpoint:

```solidity
for (uint256 i; i < rateReceiversLength;) {
    (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
        .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

    ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
        dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
    );
    unchecked { ++i; }
}
``` [1](#0-0) 

There is no cap enforced on `rateReceivers.length`. Receivers are added via `addRateReceiver()` which simply pushes to the array:

```solidity
function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
    rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));
``` [2](#0-1) 

Each loop iteration consumes substantial gas: a `staticcall` to `estimateFees`, a state-changing `send` with ETH transfer, and memory allocations for `remoteAndLocalAddresses` and `_payload`. As rsETH expands to additional chains, the per-call gas cost accumulates until the function reverts with out-of-gas on every invocation.

`RSETHMultiChainRateProvider` inherits this exact logic and is the live contract providing rsETH rates cross-chain: [3](#0-2) 

### Impact Explanation
`updateRate()` is the **only** function that pushes the rsETH/ETH exchange rate to destination chains. If it becomes uncallable due to gas exhaustion, all registered `RSETHRateReceiver` contracts on destination chains permanently hold a stale rate. rsETH holders and integrating DeFi protocols on those chains will price rsETH incorrectly, leading to mispriced swaps, incorrect collateral valuations, and loss of yield. This matches **Medium — Unbounded gas consumption** and **Low — Contract fails to deliver promised returns**.

### Likelihood Explanation
rsETH is already deployed across multiple chains (Arbitrum, Optimism, etc.) and the protocol's stated goal is continued multi-chain expansion. Each new chain deployment requires a call to `addRateReceiver()`, which is normal protocol operation — not an attack. The gas cost per receiver is high (two external calls to LayerZero per iteration), so the threshold is reached with a relatively small number of chains. The function is callable by anyone (`external payable`, no role check), so any caller will hit the same gas wall once the array is large enough.

### Recommendation
1. **Enforce a maximum cap** on `rateReceivers.length` in `addRateReceiver()`.
2. **Allow partial/indexed updates**: add an overload `updateRate(uint256 fromIndex, uint256 toIndex)` so callers can broadcast to a subset of receivers per transaction.
3. Alternatively, emit an on-chain event and let each destination chain pull the rate via a separate permissionless relay, removing the push-loop entirely.

### Proof of Concept
1. Owner calls `addRateReceiver()` for each new destination chain as rsETH expands (e.g., 20+ chains).
2. Any caller invokes `updateRate{ value: totalFee }()`.
3. The loop executes `estimateFees` + `send` for each of the 20+ receivers sequentially.
4. Gas consumed per iteration ≈ 50,000–100,000 gas (LayerZero `send` with cross-chain messaging overhead); at 20 receivers this is 1M–2M gas, scaling linearly.
5. Beyond a protocol-specific threshold, the transaction reverts with out-of-gas on every call.
6. No receiver on any destination chain can receive an updated rsETH rate; `RSETHRateReceiver.rate` on all chains is permanently stale. [4](#0-3) [3](#0-2)

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L72-75)
```text
    function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
        rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));

        emit RateReceiverAdded(_chainId, _contract);
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

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L9-28)
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
```
