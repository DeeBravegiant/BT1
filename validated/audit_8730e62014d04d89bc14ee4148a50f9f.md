### Title
Missing Zero-Address Validation in `updateRateReceiver()` Silently Breaks Cross-Chain rsETH Rate Oracle - (File: contracts/cross-chain/CrossChainRateProvider.sol)

### Summary
`CrossChainRateProvider.updateRateReceiver()` accepts a `_rateReceiver` address and stores it without any zero-address check. If the owner accidentally sets `rateReceiver` to `address(0)`, every subsequent public call to `updateRate()` encodes `address(0)` as the LayerZero destination, silently discarding all rate messages. The `RSETHRateReceiver` contracts on all supported L2s then serve a permanently stale rsETH/ETH rate until the owner corrects the value.

### Finding Description
`CrossChainRateProvider` is the abstract base for `RSETHRateProvider` (deployed on Ethereum mainnet). It exposes two admin setters that accept critical address parameters with no zero-address guard:

```solidity
// contracts/cross-chain/CrossChainRateProvider.sol L66-70
function updateRateReceiver(address _rateReceiver) external onlyOwner {
    rateReceiver = _rateReceiver;          // ← no UtilLib.checkNonZeroAddress()
    emit RateReceiverUpdated(_rateReceiver);
}
```

The public `updateRate()` function then uses `rateReceiver` directly:

```solidity
// L88
bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceiver, address(this));
// L96-98
ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
    dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
);
```

If `rateReceiver == address(0)`, LayerZero encodes `address(0)` as the remote contract. The message is either silently dropped by the endpoint or delivered to a non-existent contract on the destination chain. In either case the `RSETHRateReceiver.lzReceive()` is never called, so `rate` and `lastUpdated` on every L2 receiver freeze at their last value.

The same pattern exists for `updateLayerZeroEndpoint()` (L57-61) and is mirrored in `CrossChainRateReceiver.updateRateProvider()` / `updateLayerZeroEndpoint()` (L54-67), none of which call `UtilLib.checkNonZeroAddress()`.

### Impact Explanation
All L2 pools and wrappers that consume `RSETHRateReceiver.getRate()` for swap pricing receive a stale rsETH/ETH rate. Users depositing into `RSETHPool`, `RSETHPoolV3`, `RSETHPoolNoWrapper`, and similar contracts on Arbitrum, Optimism, Polygon zkEVM, Blast, Mode, Scroll, etc. will receive incorrect rsETH amounts calculated against an outdated rate. The contract fails to deliver its promised cross-chain rate feed without any on-chain indication of the failure.

**Impact class:** Low — Contract fails to deliver promised returns, but does not directly lose user funds.

### Likelihood Explanation
The owner must call `updateRateReceiver(address(0))` — an accidental typo or a scripting error during a routine address rotation. No malicious actor is required; the missing guard means a single mistaken transaction is sufficient. The `RSETHRateProvider` is a live mainnet contract (`0xF1cccBa5558D31628216489A1435e068b1fd2C8A` per README), making the risk concrete.

### Recommendation
Apply `UtilLib.checkNonZeroAddress()` to every address setter in both `CrossChainRateProvider` and `CrossChainRateReceiver`, consistent with the pattern used throughout the rest of the codebase:

```solidity
function updateRateReceiver(address _rateReceiver) external onlyOwner {
    UtilLib.checkNonZeroAddress(_rateReceiver);   // add this
    rateReceiver = _rateReceiver;
    emit RateReceiverUpdated(_rateReceiver);
}

function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
    UtilLib.checkNonZeroAddress(_layerZeroEndpoint); // add this
    layerZeroEndpoint = _layerZeroEndpoint;
    emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
}
```

Apply the same fix to `CrossChainRateReceiver.updateRateProvider()` and `CrossChainRateReceiver.updateLayerZeroEndpoint()`.

### Proof of Concept
1. Owner calls `RSETHRateProvider.updateRateReceiver(address(0))`.
2. `rateReceiver` is stored as `address(0)`. No revert occurs.
3. Any caller (public, no access control) calls `updateRate{ value: fee }()`.
4. `remoteAndLocalAddresses = abi.encodePacked(address(0), address(this))` — destination is the zero address.
5. LayerZero endpoint accepts the send; the message is routed to `address(0)` on the destination chain.
6. `RSETHRateReceiver.lzReceive()` is never invoked; `rate` and `lastUpdated` remain frozen.
7. Every L2 pool calling `IOracle(rsETHOracle).getRate()` returns the stale value indefinitely, mispricing all subsequent user swaps. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L57-61)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L66-70)
```text
    function updateRateReceiver(address _rateReceiver) external onlyOwner {
        rateReceiver = _rateReceiver;

        emit RateReceiverUpdated(_rateReceiver);
    }
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L85-98)
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
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L54-67)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }

    /// @notice Updates the RateProvider address
    /// @dev Can only be called by owner
    /// @param _rateProvider the new rate provider address
    function updateRateProvider(address _rateProvider) external onlyOwner {
        rateProvider = _rateProvider;

        emit RateProviderUpdated(_rateProvider);
    }
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L10-24)
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
```

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
