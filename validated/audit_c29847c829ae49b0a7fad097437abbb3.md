### Title
Hardcoded `gasLimit = 0` in `ScrollMessenger` Causes L1 Message Execution to Fail, Temporarily Freezing Bridged ETH - (File: contracts/bridges/ScrollMessenger.sol)

---

### Summary

`ScrollMessenger.sol` hardcodes `gasLimit = 0` when calling Scroll's native `sendMessage`, unlike every other messenger in the codebase which uses `DEFAULT_GAS_LIMIT = 200_000`. When the BRIDGER_ROLE triggers ETH bridging from Scroll L2 to L1, the cross-chain message is dispatched with zero gas for L1 execution. Because the recipient (`l1VaultETHForL2Chain`, i.e., `L1VaultV2`) is a contract, the L1 relay will fail out-of-gas, leaving the ETH frozen inside the Scroll bridge until a manual replay is performed.

---

### Finding Description

`OptimismMessenger`, `BaseMessenger`, and `UnichainMessenger` all declare and use a `DEFAULT_GAS_LIMIT = 200_000` constant: [1](#0-0) [2](#0-1) [3](#0-2) 

`ScrollMessenger` diverges from this pattern. Its `sendETHToL1ViaBridge` implementation passes a literal `0` as the `gasLimit` argument to `IScrollMessenger.sendMessage`: [4](#0-3) 

The inline comment claims `0` activates a "default gas limit", but Scroll's `L2ScrollMessenger` has no such fallback — `gasLimit` is encoded verbatim into the cross-chain message hash and forwarded to the L1 relayer, which executes the call with exactly that gas budget.

The call site in `RSETHPoolV3ExternalBridge.bridgeAssetsViaNativeBridge` forwards the entire ETH balance minus fees through this messenger: [5](#0-4) 

The `IL2Messenger` interface confirms the value is forwarded as `msg.value`: [6](#0-5) 

Once `sendMessage` is called, the ETH is locked inside the Scroll L2 bridge contract. On L1, the Scroll sequencer relays the message and attempts `l1VaultETHForL2Chain.call{value: amount, gas: 0}("")`. Because `L1VaultV2` is a contract (not an EOA), the call reverts immediately with out-of-gas, and the ETH cannot be delivered.

---

### Impact Explanation

All ETH accumulated in the pool and sent through `bridgeAssetsViaNativeBridge` on Scroll will be frozen inside the Scroll bridge. Recovery requires a manual `replayMessage` call on L1 with a corrected gas limit — an off-chain emergency action that is not part of normal protocol operation. Until replayed, user-deposited ETH is inaccessible. Impact: **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

The bug triggers deterministically every time `bridgeAssetsViaNativeBridge` is called on a Scroll deployment. No special conditions, attacker action, or race are required. The BRIDGER_ROLE executes this as routine protocol maintenance. Likelihood: **High**.

---

### Recommendation

Replace the hardcoded `0` with a constant matching the other messengers:

```solidity
uint32 public constant DEFAULT_GAS_LIMIT = 200_000;

function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value != value) revert MismatchedMsgValue();
    IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", DEFAULT_GAS_LIMIT, msg.sender);
}
```

The gas limit should be sized to cover the `receive()` / `fallback()` logic of `L1VaultV2`. 200,000 is the value already validated for Optimism, Base, and Unichain deployments.

---

### Proof of Concept

1. Users deposit ETH into the pool on Scroll; `wrsETH` is minted to them.
2. ETH accumulates in the pool contract.
3. BRIDGER_ROLE calls `RSETHPoolV3ExternalBridge.bridgeAssetsViaNativeBridge()`.
4. The function calls `ScrollMessenger.sendETHToL1ViaBridge{value: ethBalanceMinusFees}(l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees)`.
5. `ScrollMessenger` calls `IScrollMessenger(l2bridge).sendMessage{value: ethBalanceMinusFees}(l1VaultETHForL2Chain, ethBalanceMinusFees, "", 0, msg.sender)` — ETH is now locked in the Scroll L2 bridge.
6. The Scroll sequencer relays the message to L1 and executes `L1VaultV2.call{value: ethBalanceMinusFees, gas: 0}("")`.
7. The call reverts (out-of-gas); the message is marked failed on L1.
8. All bridged ETH is frozen until a manual `replayMessage` with a non-zero gas limit is submitted on L1. [4](#0-3) [7](#0-6)

### Citations

**File:** contracts/bridges/OptimismMessenger.sol (L16-16)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;
```

**File:** contracts/bridges/BaseMessenger.sol (L15-15)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;
```

**File:** contracts/bridges/UnichainMessenger.sol (L16-16)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;
```

**File:** contracts/bridges/ScrollMessenger.sol (L21-24)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L651-663)
```text
    function bridgeAssetsViaNativeBridge() external nonReentrant onlyRole(BRIDGER_ROLE) {
        UtilLib.checkNonZeroAddress(l2Bridge);
        UtilLib.checkNonZeroAddress(messenger);
        UtilLib.checkNonZeroAddress(l1VaultETHForL2Chain);

        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/interfaces/L2/IL2Messenger.sol (L18-18)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable;
```
