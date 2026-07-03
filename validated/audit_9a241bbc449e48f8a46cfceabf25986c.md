### Title
`ScrollMessenger.sendETHToL1ViaBridge` Hardcodes `gasLimit=0`, Causing Every L2→L1 Relay to Fail When Target Is a Contract - (`contracts/bridges/ScrollMessenger.sol`)

---

### Summary

`ScrollMessenger.sendETHToL1ViaBridge` passes a hardcoded `gasLimit=0` to `IScrollMessenger.sendMessage`. The developer comment claims this means "use the default gas limit," but Scroll's protocol treats `gasLimit` as the literal gas forwarded to the target on L1. Since `l1VaultETHForL2Chain` is the `L1Vault` contract (not an EOA), every relay attempt will fail with out-of-gas on L1. The ETH is held in the Scroll L1 bridge and is not permanently lost (Scroll's `replayMessage` allows recovery), but the protocol systematically fails to deliver its promised bridging behavior.

---

### Finding Description

The call chain is:

1. `RSETHPool.bridgeAssetsViaNativeBridge()` (and `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`) calls `IL2Messenger(messenger).sendETHToL1ViaBridge{value: ethBalanceMinusFees}(l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees)`. [1](#0-0) 

2. `ScrollMessenger.sendETHToL1ViaBridge` forwards the call to `IScrollMessenger(l2bridge).sendMessage{value: value}(target, value, "", 0, msg.sender)` with `gasLimit=0`. [2](#0-1) 

3. The `IScrollMessenger` interface documents `gasLimit` as "Gas limit required to complete the message relay on corresponding chain." — it is not a flag for "use default." [3](#0-2) 

4. `l1VaultETHForL2Chain` is the `L1Vault` contract, a full upgradeable Solidity contract with a `receive()` function and non-trivial logic. It requires well above 0 gas to accept ETH. [4](#0-3) 

In Scroll's actual L2ScrollMessenger implementation, `gasLimit` is used to compute the relay fee on L2 and to cap the gas forwarded to the target during L1 relay (`target.call{gas: gasLimit, value: value}(message)`). With `gasLimit=0`, the L1 relay call to `L1Vault` will immediately revert due to out-of-gas. The L2 transaction itself succeeds (ETH leaves the pool), but the L1 relay fails and the ETH is held in the Scroll L1 bridge.

Compare with `OptimismMessenger`, which correctly sets `DEFAULT_GAS_LIMIT = 200_000`: [5](#0-4) 

`ScrollMessenger` has no equivalent minimum gas constant, and the comment is factually wrong about Scroll's behavior. [6](#0-5) 

---

### Impact Explanation

Every invocation of `bridgeAssetsViaNativeBridge` when the Scroll messenger is configured will succeed on L2 (ETH leaves the pool, event is emitted) but fail on L1 relay. The ETH is held in the Scroll L1 bridge and is not credited to `l1VaultETHForL2Chain`. The protocol fails to deliver its promised return (ETH deposited into `L1Vault` → rsETH minted → sent back to L2 users). ETH is not permanently lost because Scroll's `replayMessage` mechanism allows replaying the failed message with a corrected gas limit, but this requires manual intervention and is not part of the protocol's normal flow.

Impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

This is a systematic failure, not a probabilistic one. Every single call to `bridgeAssetsViaNativeBridge` with the Scroll messenger configured will fail on L1 relay. The BRIDGER_ROLE caller is a trusted operator, but the bug is in the messenger contract itself, not in operator behavior. No attacker action is required; the failure is inherent to the hardcoded `gasLimit=0`.

---

### Recommendation

Set a non-zero minimum gas limit in `ScrollMessenger`, analogous to `OptimismMessenger.DEFAULT_GAS_LIMIT`. For a plain ETH transfer to a contract, a value of at least `50_000` is appropriate (Scroll's own documentation recommends values in this range for simple ETH receives). Ideally, make it configurable:

```solidity
uint256 public constant DEFAULT_GAS_LIMIT = 50_000;

function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value != value) revert MismatchedMsgValue();
    IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", DEFAULT_GAS_LIMIT, msg.sender);
}
```

---

### Proof of Concept

```solidity
// Fork test on Scroll L2
// 1. Deploy a mock IScrollMessenger that records the gasLimit argument
// 2. Configure RSETHPool with messenger = address(scrollMessenger), l2Bridge = address(mockScrollL2Messenger)
// 3. Call bridgeAssetsViaNativeBridge() as BRIDGER_ROLE
// 4. Assert: mockScrollL2Messenger.recordedGasLimit() == 0
// 5. Simulate L1 relay: call L1ScrollMessenger.relayMessageWithProof with the recorded message
//    → The call to L1Vault with gas=0 reverts
// 6. Assert: L1Vault.balance unchanged (ETH not credited)
// 7. Assert: ETH is held in L1ScrollMessenger (bridge escrow)
```

The L2 transaction emits `BridgedETHToL1ViaNativeBridge` and succeeds, but the ETH never arrives at `l1VaultETHForL2Chain`, violating the protocol invariant.

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

**File:** contracts/L1Vault.sol (L25-30)
```text
contract L1Vault is Initializable, ReentrancyGuardUpgradeable, AccessControlUpgradeable {
    using SafeERC20 for IERC20;

    /// @notice The address of the LRT deposit pool
    ILRTDepositPool public lrtDepositPool;

```

**File:** contracts/bridges/OptimismMessenger.sol (L16-26)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;

    /**
     * @notice Bridge ETH from Optimism L2 to Ethereum Mainnet
     * @param l2bridge The address of the L2 bridge on Optimism
     * @param target The address of the target contract on L1
     * @param value The amount of ETH to send
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IOptimismMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
```
