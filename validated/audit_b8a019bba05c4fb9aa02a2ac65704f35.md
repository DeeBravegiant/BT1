### Title
`CrossChainRateProvider.updateRate()` Passes Arbitrary `msg.value` to LayerZero Without Fee Estimation, Causing Silent Rate Propagation Failure - (File: contracts/cross-chain/CrossChainRateProvider.sol)

### Summary
`CrossChainRateProvider.updateRate()` forwards `msg.value` directly to the LayerZero v1 `send()` call without first calling `estimateFees()` to validate sufficiency. In LayerZero v1, an under-funded `send()` call can succeed on the source chain (ETH is consumed, storage is written) while the cross-chain message is never relayed to the destination. The concrete deployment `RSETHRateProvider` inherits this flaw and exposes no on-chain fee-estimation helper, leaving callers with no way to determine the correct fee.

### Finding Description
`CrossChainRateProvider.updateRate()` is an unrestricted `external payable` function. It writes `rate` and `lastUpdated` to storage, then calls:

```solidity
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
``` [1](#0-0) 

`msg.value` is passed verbatim with no lower-bound check and no prior call to `ILayerZeroEndpoint.estimateFees()`. The LayerZero v1 endpoint accepts the call and the source-chain transaction succeeds regardless of whether the fee is sufficient; the relayer silently drops or stores the message if the fee is too low. The concrete contract `RSETHRateProvider` inherits `updateRate()` unchanged and adds no `estimateFees` wrapper. [2](#0-1) 

By contrast, `MultiChainRateProvider.updateRate()` calls `estimateFees()` per receiver before each `send()`, demonstrating that the protocol is aware of the pattern but did not apply it to `CrossChainRateProvider`. [3](#0-2) 

### Impact Explanation
The `CrossChainRateReceiver` on L2 is the oracle that L2 pools use to price rsETH deposits and withdrawals. If `updateRate()` is called with insufficient `msg.value`, the source-chain state (`rate`, `lastUpdated`) is updated but the L2 receiver never receives the new rate. The L2 oracle becomes permanently stale until a correctly-funded call is made. Because rsETH accrues staking yield over time, a stale (lower) rate causes L2 depositors to receive more wrsETH than they are entitled to, draining protocol-owned yield. This maps to **Low — Contract fails to deliver promised returns**: the rate propagation mechanism silently fails to deliver the promised cross-chain rate update. [4](#0-3) 

### Likelihood Explanation
`updateRate()` has no access control — any externally-owned account can call it. A caller who sends `msg.value = 0` or an amount below the LayerZero relayer threshold will trigger the silent failure. This is easy to do accidentally (off-chain tooling that does not pre-query fees) or deliberately (griefing the oracle). The `CrossChainRateProvider` exposes no `estimateFees()` helper, so there is no on-chain guidance for callers. [5](#0-4) 

### Recommendation
Add an `estimateFees()` view function to `CrossChainRateProvider` (mirroring the pattern in `MultiChainRateProvider`) and enforce a minimum-fee check inside `updateRate()`:

```solidity
function estimateFees() public view returns (uint256 nativeFee) {
    bytes memory _payload = abi.encode(getLatestRate());
    (nativeFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
        .estimateFees(dstChainId, address(this), _payload, false, bytes(""));
}

function updateRate() external payable nonReentrant {
    uint256 requiredFee = estimateFees();
    require(msg.value >= requiredFee, "INSUFFICIENT_FEE");
    // ... existing logic
}
```

### Proof of Concept
1. Deploy `RSETHRateProvider` pointing to a live LayerZero endpoint and a `CrossChainRateReceiver` on an L2.
2. Call `RSETHRateProvider.updateRate{ value: 0 }()`.
3. The source-chain transaction succeeds: `rate` and `lastUpdated` are written, `RateUpdated` is emitted.
4. The LayerZero relayer receives a message with zero fee and does not relay it.
5. The `CrossChainRateReceiver` on L2 retains its previous (stale) rate indefinitely.
6. Any L2 pool reading `CrossChainRateReceiver.getRate()` now prices rsETH at the old, lower rate, allowing depositors to extract excess wrsETH relative to the true rsETH/ETH exchange rate. [1](#0-0) [2](#0-1)

### Citations

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

**File:** contracts/cross-chain/RSETHRateProvider.sol (L10-34)
```text
contract RSETHRateProvider is CrossChainRateProvider {
    address public immutable rsETHPriceOracle;

    constructor(address _rsETHPriceOracle, uint16 _dstChainId, address _layerZeroEndpoint) {
        rsETHPriceOracle = _rsETHPriceOracle;

        rateInfo = RateInfo({
            tokenSymbol: "rsETH",
            tokenAddress: 0xA1290d69c65A6Fe4DF752f95823fae25cB99e5A7, // rsETH token address on ETH mainnet
            baseTokenSymbol: "ETH",
            baseTokenAddress: address(0) // Address 0 for native tokens
        });
        dstChainId = _dstChainId;
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

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L124-129)
```text
            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-100)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");

        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }
```
