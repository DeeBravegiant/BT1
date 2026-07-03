### Title
Excess `msg.value` Permanently Trapped in `MultiChainRateProvider.updateRate()` Due to No Refund After Multi-Message Loop - (File: contracts/cross-chain/MultiChainRateProvider.sol)

### Summary
`MultiChainRateProvider.updateRate()` is a public `payable` function that sends one LayerZero message per registered receiver in a loop, forwarding exactly the on-chain `estimatedFee` for each. Any `msg.value` exceeding the sum of all per-receiver fees is permanently trapped in the contract, which has no ETH recovery mechanism.

### Finding Description
`updateRate()` in `contracts/cross-chain/MultiChainRateProvider.sol` iterates over all `rateReceivers` and for each one calls `ILayerZeroEndpoint.estimateFees()` on-chain, then immediately sends exactly that `estimatedFee` to the endpoint:

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

The function neither:
1. Validates that `msg.value >= sum(estimatedFee)` before the loop begins, nor
2. Refunds `msg.value - sum(estimatedFee)` after the loop ends.

The concrete implementations `RSETHMultiChainRateProvider` and `AGETHMultiChainRateProvider` both inherit only from `MultiChainRateProvider` (which inherits `Ownable` and `ReentrancyGuard`). Neither the abstract base nor either concrete implementation contains any ETH withdrawal or recovery function. The `Recoverable` utility contract (`contracts/utils/Recoverable.sol`) exists in the codebase but is not used here.

Callers are expected to supply `msg.value` covering all fees. Because on-chain fee estimation can differ slightly from the actual fee consumed (e.g., due to block-to-block gas price changes, or callers deliberately over-paying to avoid mid-loop reverts), any surplus ETH is permanently locked.

### Impact Explanation
Any ETH sent as `msg.value` beyond the exact sum of per-receiver `estimatedFee` values is permanently frozen in the deployed `RSETHMultiChainRateProvider` / `AGETHMultiChainRateProvider` contracts. There is no admin rescue path, no `receive()` + withdrawal pattern, and no `recoverETH()` function. Over repeated calls the trapped balance accumulates irreversibly.

This matches the **permanent freezing of funds** impact class.

### Likelihood Explanation
`updateRate()` has no access-control modifier — any external account can call it. The `estimateTotalFee()` view helper is provided for callers to pre-compute the required value, but:
- Fee estimates are stale by the time the transaction is mined (gas price fluctuations).
- Callers routinely add a small buffer to avoid mid-loop reverts.
- The protocol itself calls this function to push rates to 10+ chains (Base, Linea, zkSync, Unichain, TAC, Avalanche, Sonic, Ink, Plasma, Stable, etc.), making over-payment a near-certainty in practice.

### Recommendation
After the loop, refund any unspent ETH to `msg.sender`:

```solidity
function updateRate() external payable nonReentrant {
    ...
    for (...) { ... }

    // Refund excess native fee
    uint256 remaining = address(this).balance;
    if (remaining > 0) {
        (bool ok,) = payable(msg.sender).call{ value: remaining }("");
        require(ok, "refund failed");
    }

    emit RateUpdated(rate);
}
```

Alternatively, inherit from `Recoverable` to allow an admin to rescue trapped ETH, or add a dedicated `withdrawETH()` function restricted to the owner.

### Proof of Concept

1. `RSETHMultiChainRateProvider` is deployed on Ethereum mainnet with 10 `rateReceivers` (Base, Linea, zkSync, Unichain, TAC, Avalanche, Sonic, Ink, Plasma, Stable).
2. Caller queries `estimateTotalFee()` → returns `X` wei.
3. Caller calls `updateRate{ value: X + 1e15 }()` (adds 0.001 ETH buffer to avoid revert).
4. The loop sends exactly `estimatedFee_i` for each of the 10 receivers, consuming `X` wei total.
5. The remaining `1e15` wei stays in the contract.
6. No function exists to withdraw it. The ETH is permanently frozen.

Relevant code: [1](#0-0) 

Concrete implementations with no ETH recovery: [2](#0-1) [3](#0-2)

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

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L9-33)
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
```

**File:** contracts/agETH/AGETHMultiChainRateProvider.sol (L12-36)
```text
contract AGETHMultiChainRateProvider is MultiChainRateProvider {
    address public immutable agETHPriceOracle;

    constructor(address _agETHPriceOracle, address _layerZeroEndpoint) {
        agETHPriceOracle = _agETHPriceOracle;

        rateInfo = RateInfo({
            tokenSymbol: "agETH",
            tokenAddress: 0xe1B4d34E8754600962Cd944B535180Bd758E6c2e, // agETH token address on ETH mainnet
            baseTokenSymbol: "ETH",
            baseTokenAddress: address(0) // Address 0 for native tokens
        });

        layerZeroEndpoint = _layerZeroEndpoint;
    }

    /// @notice Returns the latest rate from the agETH rate provider contract
    function getLatestRate() public view override returns (uint256) {
        return IAgEthRateProvider(agETHPriceOracle).getRate();
    }

    /// @notice Calls the getLatestRate function and returns the rate
    function getRate() external view returns (uint256) {
        return getLatestRate();
    }
```
