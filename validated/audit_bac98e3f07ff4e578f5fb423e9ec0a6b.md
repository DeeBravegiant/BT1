### Title
Blocking `lzReceive` Without `forceResumeReceive` Permanently Freezes Rate Oracle Updates — (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary
`CrossChainRateReceiver` implements LayerZero's blocking receiver pattern (`ILayerZeroReceiver`) without implementing `ILayerZeroUserApplicationConfig`. This means there is no `forceResumeReceive` escape hatch. If `lzReceive` reverts for any reason, the LayerZero v1 endpoint stores the payload and permanently blocks the inbound message queue with no on-chain recovery path. Both `RSETHRateReceiver` and `AGETHRateReceiver` inherit this flaw.

---

### Finding Description

`CrossChainRateReceiver` is an abstract contract that directly implements `ILayerZeroReceiver` and provides a blocking `lzReceive`: [1](#0-0) 

```solidity
abstract contract CrossChainRateReceiver is ILayerZeroReceiver, Ownable {
```

The `lzReceive` function contains three `require` statements that can revert: [2](#0-1) 

```solidity
function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
    require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");
    ...
    require(_srcChainId == srcChainId, "Src chainId must be correct");
    require(srcAddress == rateProvider, "Src address must be provider");
    uint256 _rate = abi.decode(_payload, (uint256));
```

In LayerZero v1's blocking mode, any revert inside `lzReceive` causes the endpoint to store the payload and halt all subsequent inbound messages from that source. The contract does **not** implement `ILayerZeroUserApplicationConfig`: [3](#0-2) 

```solidity
// @notice Only when the UA needs to resume the message flow in blocking mode and clear the stored payload
// @param _srcChainId - the chainId of the source chain
// @param _srcAddress - the contract address of the source contract at the source chain
function forceResumeReceive(uint16 _srcChainId, bytes calldata _srcAddress) external;
```

Without `forceResumeReceive`, there is no on-chain mechanism to clear a stored payload and unblock the queue. Both concrete receivers inherit this flaw: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

The `rate` stored in `CrossChainRateReceiver` is consumed by pool contracts via `rsETHOracle.getRate()` to price deposits and mints of wrapped rsETH (wrsETH). If the inbound queue is permanently blocked, `rate` and `lastUpdated` are frozen at their last values. All pools relying on this oracle will operate on a permanently stale rsETH/ETH exchange rate, causing users to receive incorrect amounts of wrsETH on deposit — the contract fails to deliver its promised rate-based returns indefinitely with no recovery path.

**Impact: Low — Contract fails to deliver promised returns (stale rate oracle, no value loss but incorrect accounting).**

---

### Likelihood Explanation

A revert in `lzReceive` can occur if:
- The owner calls `updateRateProvider` or `updateSrcChainId` while a message is already in-flight (the in-flight message carries the old `srcAddress`/`_srcChainId`, failing the `require` checks).
- `abi.decode(_payload, (uint256))` panics due to a malformed payload from the source chain provider.

The first scenario is realistic during any protocol migration or provider address rotation. The second depends on source-chain correctness. Either way, once triggered, the block is permanent and unrecoverable on-chain.

**Likelihood: Low** — requires a specific timing condition (in-flight message during config update) or source-chain bug, but the consequence is irreversible.

---

### Recommendation

1. Implement `ILayerZeroUserApplicationConfig` in `CrossChainRateReceiver` to expose `forceResumeReceive`, allowing the owner to clear a stored payload and unblock the queue.
2. Adopt the nonblocking receiver pattern: wrap the core logic of `lzReceive` in a `try/catch`, emit a failure event on revert, and store failed payloads for manual retry — preventing any single bad message from blocking all future messages.
3. Ensure `setTrustedRemote()` is called on all deployed instances to restrict inbound messages to known sources.

---

### Proof of Concept

1. `RSETHRateReceiver` is deployed; `rateProvider` is set to address `A` on source chain.
2. A rate update message from `A` is submitted on the source chain and is in-flight in the LayerZero relayer queue.
3. Owner calls `updateRateProvider(B)` on `RSETHRateReceiver` to rotate the provider.
4. The in-flight message arrives; `lzReceive` is called by the endpoint. `srcAddress == A` but `rateProvider == B`, so `require(srcAddress == rateProvider)` reverts.
5. LayerZero v1 endpoint stores the payload. All subsequent rate update messages from `B` are now queued behind the stored payload and will never be delivered.
6. `rate` is permanently frozen. `CrossChainRateReceiver.getRate()` returns the stale value forever.
7. All pools using this oracle price deposits against the stale rate indefinitely, with no on-chain recovery available. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L11-11)
```text
abstract contract CrossChainRateReceiver is ILayerZeroReceiver, Ownable {
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L60-76)
```text
    /// @notice Updates the RateProvider address
    /// @dev Can only be called by owner
    /// @param _rateProvider the new rate provider address
    function updateRateProvider(address _rateProvider) external onlyOwner {
        rateProvider = _rateProvider;

        emit RateProviderUpdated(_rateProvider);
    }

    /// @notice Updates the source chainId
    /// @dev Can only be called by owner
    /// @param _srcChainId the source chainId
    function updateSrcChainId(uint16 _srcChainId) external onlyOwner {
        srcChainId = _srcChainId;

        emit SrcChainIdUpdated(_srcChainId);
    }
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

**File:** contracts/external/layerzero/interfaces/ILayerZeroUserApplicationConfig.sol (L21-24)
```text
    // @notice Only when the UA needs to resume the message flow in blocking mode and clear the stored payload
    // @param _srcChainId - the chainId of the source chain
    // @param _srcAddress - the contract address of the source contract at the source chain
    function forceResumeReceive(uint16 _srcChainId, bytes calldata _srcAddress) external;
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L9-15)
```text
contract RSETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/agETH/AGETHRateReceiver.sol (L9-15)
```text
contract AGETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "agETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```
