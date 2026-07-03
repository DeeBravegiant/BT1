### Title
In-flight LayerZero Message Blocked by `updateSrcChainId` Race Condition with No Recovery Path — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.updateSrcChainId` immediately overwrites `srcChainId` with no guard for in-flight messages. If the owner migrates the source chain ID while a message is already in transit, `lzReceive` will revert on the stale `_srcChainId`, causing the LayerZero v1 endpoint to store a blocked payload. Because the contract implements `ILayerZeroReceiver` but **not** `ILayerZeroUserApplicationConfig`, it exposes no `forceResumeReceive` wrapper, making the channel permanently unrecoverable without a contract upgrade.

---

### Finding Description

`updateSrcChainId` performs an immediate, unconditional write: [1](#0-0) 

`lzReceive` then validates the incoming chain ID against the current storage value: [2](#0-1) 

LayerZero v1's `receivePayload` calls `lzReceive` and, on revert, stores the payload at the endpoint and halts all further ordered delivery for that `(srcChainId, srcAddress)` pair. Recovery requires the UA to call `endpoint.forceResumeReceive(srcChainId, srcAddress)`. However, `CrossChainRateReceiver` only inherits `ILayerZeroReceiver`: [3](#0-2) 

It does **not** inherit or implement `ILayerZeroUserApplicationConfig`: [4](#0-3) 

Because `forceResumeReceive` on the endpoint requires `msg.sender == _dstAddress` (the UA contract itself), and the UA has no function to forward that call, the blocked payload cannot be cleared without a contract upgrade or redeployment.

---

### Impact Explanation

Once the channel is blocked, all subsequent rate-update messages from the old `(srcChainId, rateProvider)` pair are also queued behind the stuck payload. `AGETHRateReceiver.rate` and `lastUpdated` stop advancing. Any downstream protocol that reads `getRate()` for pricing, minting, or collateral valuation will operate on a permanently stale rate, constituting at minimum a **medium — temporary (in practice permanent without upgrade) freezing of rate-dependent fund flows**. [5](#0-4) 

---

### Likelihood Explanation

The trigger is a legitimate owner operation — migrating the source chain ID — not a key compromise. Any `updateSrcChainId` call that races with an in-flight message (a window that can span minutes on congested chains) produces the condition. The owner has no on-chain tooling to detect pending in-flight messages before calling the function.

---

### Recommendation

1. Add a `forceResumeReceive` passthrough so the owner can unblock the endpoint channel:

```solidity
function forceResumeReceive(uint16 _srcChainId, bytes calldata _srcAddress) external onlyOwner {
    ILayerZeroEndpoint(layerZeroEndpoint).forceResumeReceive(_srcChainId, _srcAddress);
}
```

2. Optionally, document that `updateSrcChainId` should only be called after confirming no in-flight messages exist (via `endpoint.getInboundNonce`).

---

### Proof of Concept

```solidity
// 1. Deploy MockLZEndpoint, CrossChainRateReceiver with srcChainId=1, rateProvider=PROVIDER
// 2. Simulate in-flight: endpoint queues a message from chainId=1, PROVIDER
// 3. Owner calls: receiver.updateSrcChainId(2)
// 4. Endpoint delivers message: endpoint.receivePayload(1, PROVIDER_BYTES, receiver, nonce, gasLimit, payload)
//    -> calls receiver.lzReceive(1, ...) -> reverts "Src chainId must be correct"
//    -> endpoint stores payload, channel blocked
// 5. Assert: endpoint.hasStoredPayload(1, PROVIDER_BYTES) == true
// 6. Attempt recovery: receiver has no forceResumeReceive -> no recovery path exists
// 7. Assert: receiver.lastUpdated() unchanged, rate frozen
```

The revert at line 90 is the root cause: [6](#0-5)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L11-11)
```text
abstract contract CrossChainRateReceiver is ILayerZeroReceiver, Ownable {
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L72-76)
```text
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
