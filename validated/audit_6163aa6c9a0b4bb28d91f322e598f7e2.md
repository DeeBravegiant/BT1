### Title
LayerZero Inbound Channel Blocking via `lzReceive` Revert in `CrossChainRateReceiver` — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

### Summary
`CrossChainRateReceiver.lzReceive` implements the blocking LayerZero receiver pattern directly. Any revert inside `lzReceive` causes the LayerZero V1 endpoint to store the payload and halt all subsequent message delivery on that channel. An attacker can deliberately trigger this revert by sending a cross-chain message from an arbitrary address on the source chain, causing the endpoint to call `lzReceive` with a `_srcAddress` that fails the `rateProvider` check. Block stuffing can then delay the owner's `forceResumeReceive` call, extending the outage.

### Finding Description

`CrossChainRateReceiver` implements `ILayerZeroReceiver` directly without the `NonblockingLzApp` wrapper: [1](#0-0) 

```solidity
function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
    require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");
    ...
    require(_srcChainId == srcChainId, "Src chainId must be correct");
    require(srcAddress == rateProvider, "Src address must be provider");
```

The LayerZero V1 endpoint does **not** filter messages by source address before calling `lzReceive` — it delivers any message originating from any address on the source chain. An attacker deploys a contract on the source chain and sends a LZ message targeting `RSETHRateReceiver` on the destination chain. The endpoint calls `lzReceive` with the attacker's address as `_srcAddress`. The `require(srcAddress == rateProvider)` check fails, the call reverts, and the endpoint stores the payload and blocks the channel. [2](#0-1) 

Once `hasStoredPayload` returns `true`, no further messages on that `(srcChainId, srcAddress)` path are delivered until `forceResumeReceive` is called on the endpoint: [3](#0-2) 

Block stuffing the destination chain fills every block with attacker-controlled transactions, preventing the owner's `forceResumeReceive` transaction from landing and extending the channel outage for as long as the attacker sustains the stuffing.

The `NonblockingLzApp` pattern avoids this by wrapping the inner logic in a `try/catch` and storing failed payloads locally, so a revert never propagates back to the endpoint. `CrossChainRateReceiver` does not use this pattern. [4](#0-3) 

### Impact Explanation
All rate updates to `RSETHRateReceiver` are frozen for the duration of the block-stuffing attack. Any protocol component consuming `getRate()` (e.g., pricing, collateral valuation) receives a stale rate. This matches **Low — Block stuffing** and **Low — Contract fails to deliver promised returns**. [5](#0-4) 

### Likelihood Explanation
Triggering the channel block requires only sending one cross-chain LZ message from any address on the source chain — a low-cost, permissionless action. Block stuffing is expensive but is the stated attack vector in scope. The channel-blocking step alone is trivially achievable; block stuffing amplifies the duration.

### Recommendation
Replace the direct `ILayerZeroReceiver` implementation with the `NonblockingLzApp` pattern: wrap the inner logic in a `try/catch` inside `lzReceive`, store failed payloads locally, and expose a `retryMessage` function. This ensures a revert in application logic never propagates to the endpoint and never blocks the channel.

### Proof of Concept
1. Fork the destination chain with a real LZ V1 endpoint.
2. Deploy `RSETHRateReceiver` with a known `srcChainId` and `rateProvider`.
3. From the source chain fork, deploy an attacker contract and call `ILayerZeroEndpoint.send(...)` targeting `RSETHRateReceiver` with a valid `srcChainId` but the attacker's own address as sender.
4. Relay the message: the endpoint calls `lzReceive`; `require(srcAddress == rateProvider)` reverts.
5. Assert `endpoint.hasStoredPayload(srcChainId, srcAddress)` returns `true`.
6. Simulate block stuffing by submitting high-gas filler transactions for N blocks; assert `forceResumeReceive` cannot land.
7. Assert that a legitimate `updateRate` message sent during this window is also queued and not delivered.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L11-11)
```text
abstract contract CrossChainRateReceiver is ILayerZeroReceiver, Ownable {
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-91)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/external/layerzero/interfaces/ILayerZeroEndpoint.sol (L80-83)
```text
    // @notice query if any STORED payload (message blocking) at the endpoint.
    // @param _srcChainId - the source chain identifier
    // @param _srcAddress - the source chain contract address
    function hasStoredPayload(uint16 _srcChainId, bytes calldata _srcAddress) external view returns (bool);
```

**File:** contracts/external/layerzero/interfaces/ILayerZeroUserApplicationConfig.sol (L21-24)
```text
    // @notice Only when the UA needs to resume the message flow in blocking mode and clear the stored payload
    // @param _srcChainId - the chainId of the source chain
    // @param _srcAddress - the contract address of the source contract at the source chain
    function forceResumeReceive(uint16 _srcChainId, bytes calldata _srcAddress) external;
```
