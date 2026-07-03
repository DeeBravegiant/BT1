Audit Report

## Title
`ScrollMessenger.sendETHToL1ViaBridge` Hardcodes `gasLimit=0`, Causing L2→L1 Relay to Fail When Target Is a Contract - (`contracts/bridges/ScrollMessenger.sol`)

## Summary
`ScrollMessenger.sendETHToL1ViaBridge` passes a hardcoded `gasLimit=0` to `IScrollMessenger.sendMessage`, with a developer comment falsely claiming this means "use the default gas limit." Scroll's protocol treats `gasLimit` as the literal gas forwarded to the target on L1 relay. Since `l1VaultETHForL2Chain` is a Solidity contract (`L1Vault`) requiring non-trivial gas to accept ETH, every L1 relay attempt will revert out-of-gas. ETH is held in the Scroll L1 bridge escrow and is not permanently lost, but the protocol systematically fails to deliver ETH to `L1Vault`.

## Finding Description
The call chain is:

1. `RSETHPool.bridgeAssetsViaNativeBridge()` (restricted to `BRIDGER_ROLE`) calls `IL2Messenger(messenger).sendETHToL1ViaBridge{value: ethBalanceMinusFees}(l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees)`. [1](#0-0) 

2. `ScrollMessenger.sendETHToL1ViaBridge` forwards to `IScrollMessenger(l2bridge).sendMessage{value: value}(target, value, "", 0, msg.sender)` with `gasLimit=0`. The developer comment `@dev Gas limit is set to 0 to use the default gas limit` is factually incorrect for Scroll's protocol. [2](#0-1) 

3. `IScrollMessenger` explicitly documents the parameter as `"Gas limit required to complete the message relay on corresponding chain."` — it is not a sentinel value for "use default." [3](#0-2) 

4. In Scroll's L1ScrollMessenger, the relay executes `target.call{gas: gasLimit, value: value}(message)`. With `gasLimit=0`, the call to `L1Vault` (an upgradeable contract with a `receive()` function and access-control logic) immediately reverts out-of-gas. The L2 transaction succeeds and ETH leaves the pool, but the L1 relay fails and ETH is held in the Scroll L1 bridge escrow.

5. By contrast, `OptimismMessenger` correctly sets `DEFAULT_GAS_LIMIT = 200_000` for the analogous call. [4](#0-3) 

No existing check in `ScrollMessenger` validates or enforces a non-zero gas limit before forwarding to the Scroll bridge.

## Impact Explanation
Every invocation of `bridgeAssetsViaNativeBridge` when the Scroll messenger is configured succeeds on L2 (ETH leaves the pool, `BridgedETHToL1ViaNativeBridge` is emitted) but fails on L1 relay. ETH is not credited to `l1VaultETHForL2Chain`, breaking the protocol invariant that deposited ETH flows through `L1Vault` to generate yield and back to L2 users as rsETH. ETH is not permanently lost (Scroll's `replayMessage` allows recovery with a corrected gas limit), but recovery requires manual intervention outside the protocol's normal flow.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
This is a systematic, deterministic failure. Every call to `bridgeAssetsViaNativeBridge` with the Scroll messenger configured will fail on L1 relay — no probabilistic conditions, no attacker action required. The `BRIDGER_ROLE` is an operational role expected to call this function regularly as part of normal protocol operation; the bug is in the contract code, not in operator behavior.

## Recommendation
Add a non-zero minimum gas constant to `ScrollMessenger`, analogous to `OptimismMessenger.DEFAULT_GAS_LIMIT`:

```solidity
uint256 public constant DEFAULT_GAS_LIMIT = 50_000;

function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value != value) revert MismatchedMsgValue();
    IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", DEFAULT_GAS_LIMIT, msg.sender);
}
```

A value of `50_000` is sufficient for a plain ETH receive to a contract and aligns with Scroll's own documentation recommendations. Making it a configurable parameter (via an admin setter) would allow adjustment without redeployment.

## Proof of Concept

```solidity
// Foundry fork test on Scroll L2
// 1. Deploy MockScrollL2Messenger that records gasLimit from sendMessage calls
// 2. Deploy ScrollMessenger (the contract under test)
// 3. Configure RSETHPool: messenger = address(scrollMessenger), l2Bridge = address(mockScrollL2Messenger)
// 4. Fund RSETHPool with ETH (simulate user deposits)
// 5. Call bridgeAssetsViaNativeBridge() as BRIDGER_ROLE
// 6. Assert: mockScrollL2Messenger.recordedGasLimit() == 0  ← confirms the bug
// 7. Simulate L1 relay: call L1ScrollMessenger.relayMessageWithProof with the recorded message
//    using gas=0 for the target call → reverts out-of-gas
// 8. Assert: L1Vault.balance unchanged (ETH not credited)
// 9. Assert: ETH balance of L1ScrollMessenger (bridge escrow) increased by bridged amount
// The L2 tx emits BridgedETHToL1ViaNativeBridge but ETH never arrives at l1VaultETHForL2Chain
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L489-491)
```text
        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```

**File:** contracts/bridges/ScrollMessenger.sol (L19-24)
```text
     * @dev Gas limit is set to 0 to use the default gas limit
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
    }
```

**File:** contracts/interfaces/L2/IScrollMessenger.sol (L62-63)
```text
    /// @param gasLimit Gas limit required to complete the message relay on corresponding chain.
    function sendMessage(address target, uint256 value, bytes calldata message, uint256 gasLimit) external payable;
```

**File:** contracts/bridges/OptimismMessenger.sol (L16-16)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;
```
